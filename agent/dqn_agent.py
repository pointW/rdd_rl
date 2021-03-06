from collections import namedtuple
import random
import time
import copy
from abc import abstractmethod
import os

import matplotlib.pyplot as plt
import numpy as np

from tqdm import tqdm, trange

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

Transition = namedtuple('Transition', ('state', 'action', 'next_state', 'reward'))


class ReplayMemory(object):
    def __init__(self, capacity):
        self.capacity = capacity
        self.memory = []
        self.position = 0

    def push(self, *args):
        state, action, next_state, reward = args
        state = state.to('cpu')
        action = action.to('cpu')
        if next_state is not None:
            next_state = next_state.to('cpu')
        reward = reward.to('cpu')

        if len(self.memory) < self.capacity:
            self.memory.append(None)
        self.memory[self.position] = Transition(state, action, next_state, reward)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.memory)


class DQNAgent:
    def __init__(self, model_class, model=None, env=None, exploration=None,
                 gamma=0.99, memory_size=100000, batch_size=64, target_update_frequency=1000, saving_dir=None):
        """
        base class for dqn agent
        :param model_class: sub class of torch.nn.Module. class reference of the model
        :param model: initial model of the policy net. could be None if loading from checkpoint
        :param env: environment
        :param exploration: exploration object. Must have function value(step) which returns e
        :param gamma: gamma
        :param memory_size: size of the memory
        :param batch_size: size of the mini batch for one step update
        :param target_update_frequency: the frequency for updating target net (in steps)
        :param saving_dir: the directory for saving checkpoint
        """
        self.model_class = model_class
        self.env = env
        self.exploration = exploration
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.policy_net = None
        self.target_net = None
        self.optimizer = None
        if model:
            self.policy_net = model
            self.target_net = copy.deepcopy(self.policy_net)
            self.policy_net = self.policy_net.to(self.device)
            self.target_net = self.target_net.to(self.device)
            self.target_net.eval()
            self.optimizer = optim.Adam(self.policy_net.parameters(), lr=0.0001)
        self.memory = ReplayMemory(memory_size)
        self.batch_size = batch_size
        self.gamma = gamma
        self.target_update = target_update_frequency
        self.steps_done = 0
        self.episodes_done = 0
        self.episode_rewards = []
        self.episode_lengths = []
        self.saving_dir = saving_dir

        self.state = None

    def forwardPolicyNet(self, state):
        """
        forward the policy net and get q values
        :param state: state passing to the policy net
        :return: tensor of action size, q values
        """
        with torch.no_grad():
            q_values = self.policy_net(state)
            return q_values

    def selectAction(self, state, require_q=False):
        """
        select action base on e-greedy policy
        :param state: the state input tensor for the network
        :param require_q: if True, return (action, q) else return action only
        :return: (1x1 tensor) action [, (float) q]
        """
        e = self.exploration.value(self.steps_done)
        self.steps_done += 1
        q_values = self.forwardPolicyNet(state)
        if random.random() > e:
            action = q_values.max(1)[1].view(1, 1)
        else:
            if hasattr(self.env, 'nA'):
                action_space = self.env.nA
            else:
                action_space = self.env.action_space.n
            action = torch.tensor([[random.randrange(action_space)]], device=self.device, dtype=torch.long)
        q_value = q_values.gather(1, action).item()
        if require_q:
            return action, q_value
        return action

    @staticmethod
    def getNonFinalNextStateBatch(mini_batch):
        """
        get non final next state batch tensor from the mini batch
        :param mini_batch:
        :return: tensor
        """
        non_final_next_states = torch.cat([s for s in mini_batch.next_state
                                           if s is not None])
        return non_final_next_states

    @staticmethod
    def getStateBatch(mini_batch):
        """
        get state batch tensor from the mini batch
        :param mini_batch:
        :return: tensor
        """
        state_batch = torch.cat(mini_batch.state)
        return state_batch

    def optimizeModel(self):
        """
        one step update for the model
        :return: None
        """
        if len(self.memory) < self.batch_size:
            return
        transitions = self.memory.sample(self.batch_size)
        mini_batch = Transition(*zip(*transitions))
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                                mini_batch.next_state)), device=self.device, dtype=torch.uint8).to(self.device)
        non_final_next_states = self.getNonFinalNextStateBatch(mini_batch).to(self.device)
        state_batch = self.getStateBatch(mini_batch).to(self.device)
        action_batch = torch.cat(mini_batch.action).to(self.device)
        reward_batch = torch.cat(mini_batch.reward).to(self.device)

        state_action_values = self.policy_net(state_batch).gather(1, action_batch)

        next_state_values = torch.zeros(self.batch_size, device=self.device)
        next_state_values[non_final_mask] = self.target_net(non_final_next_states).max(1)[0].detach()

        expected_state_action_values = (next_state_values * self.gamma) + reward_batch

        loss = F.mse_loss(state_action_values, expected_state_action_values.unsqueeze(1))

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

    def resetEnv(self):
        """
        reset the env and set self.state
        :return: None
        """
        obs = self.env.reset()
        self.state = torch.tensor(obs, device=self.device, dtype=torch.float).unsqueeze(0)
        return

    def takeAction(self, action):
        """
        take given action and return response
        :param action: int, action to take
        :return: obs_, r, done, info
        """
        return self.env.step(action)

    def getNextState(self, obs):
        """
        get the next state from observation
        :param obs: observation from env
        :return: tensor of next state
        """
        return torch.tensor(obs, device=self.device, dtype=torch.float).unsqueeze(0)

    def trainOneEpisode(self, num_episodes, max_episode_steps=100, save_freq=100, render=False):
        """
        train the network for on episode
        :param num_episodes: number of total episodes
        :param max_episode_steps: number of max steps for each episode
        :param save_freq: number of episodes per saving
        :return:
        """
        # tqdm.write('------Episode {} / {}------'.format(self.episodes_done, num_episodes))
        self.resetEnv()
        r_total = 0
        with trange(1, max_episode_steps+1, leave=False) as t:

            for step in t:
                if render:
                    self.env.render()
                state = self.state
                action, q = self.selectAction(state, require_q=True)
                obs_, r, done, info = self.takeAction(action.item())
                # if print_step:
                #     print 'step {}, action: {}, q: {}, reward: {} done: {}' \
                #         .format(step, action.item(), q, r, done)
                r_total += r
                # t.set_postfix(step='{:>5}'.format(step), q='{:>5}'.format(round(q, 4)), total_reward='{:>5}'.format(r_total))
                t.set_postfix_str('step={:>5}, q={:>5}, total_reward={:>5}'.format(step, round(q, 2), r_total))
                if done or step == max_episode_steps:
                    next_state = None
                else:
                    next_state = self.getNextState(obs_)
                reward = torch.tensor([r], device=self.device, dtype=torch.float)
                self.memory.push(state, action, next_state, reward)
                self.optimizeModel()
                if self.steps_done % self.target_update == 0:
                    self.target_net.load_state_dict(self.policy_net.state_dict())

                if done or step == max_episode_steps - 1:
                    tqdm.write('------Episode {} ended, total reward: {}, step: {}------' \
                        .format(self.episodes_done, r_total, step))
                    tqdm.write('------Total steps done: {}, current e: {} ------' \
                        .format(self.steps_done, self.exploration.value(self.steps_done)))
                    # print '------Episode {} ended, total reward: {}, step: {}------' \
                    #     .format(self.episodes_done, r_total, step)
                    # print '------Total steps done: {}, current e: {} ------' \
                    #     .format(self.steps_done, self.exploration.value(self.steps_done))
                    self.episodes_done += 1
                    self.episode_rewards.append(r_total)
                    self.episode_lengths.append(step)
                    if self.episodes_done % save_freq == 0:
                        self.saveCheckpoint()
                    break
                self.state = next_state

    def train(self, num_episodes, max_episode_steps=100, save_freq=100, render=False):
        """
        train the network for given number of episodes
        :param num_episodes:
        :param max_episode_steps:
        :param save_freq:
        :return:
        """
        while self.episodes_done < num_episodes:
            self.trainOneEpisode(num_episodes, max_episode_steps, save_freq, render)
        self.saveCheckpoint()

    def getSavingState(self):
        state = {
            'episode': self.episodes_done,
            'steps': self.steps_done,
            'policy_state_dict': self.policy_net.state_dict(),
            'target_state_dict': self.target_net.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'episode_rewards': self.episode_rewards,
            'episode_lengths': self.episode_lengths
        }
        return state

    def saveCheckpoint(self):
        """
        save checkpoint in self.saving_dir
        :return: None
        """
        time_stamp = time.strftime('%Y%m%d%H%M%S', time.gmtime())
        state_filename = os.path.join(self.saving_dir, 'checkpoint.' + time_stamp + '.pth.tar')
        mem_filename = os.path.join(self.saving_dir, 'memory.' + time_stamp + '.pth.tar')
        state = self.getSavingState()
        memory = {
            'memory': self.memory
        }
        torch.save(state, state_filename)
        torch.save(memory, mem_filename)

    def loadCheckpoint(self, time_stamp, data_only=False, load_memory=True):
        """
        load checkpoint at input time stamp
        :param time_stamp: time stamp for the checkpoint
        :return: None
        """
        state_filename = os.path.join(self.saving_dir, 'checkpoint.' + time_stamp + '.pth.tar')
        mem_filename = os.path.join(self.saving_dir, 'memory.' + time_stamp + '.pth.tar')

        print 'loading checkpoint: ', time_stamp
        checkpoint = torch.load(state_filename)
        if data_only:
            self.episode_rewards = checkpoint['episode_rewards']
            self.episode_lengths = checkpoint['episode_lengths']
            return

        self.episodes_done = checkpoint['episode']
        self.steps_done = checkpoint['steps']
        self.episode_rewards = checkpoint['episode_rewards']
        self.episode_lengths = checkpoint['episode_lengths']

        self.policy_net.load_state_dict(checkpoint['policy_state_dict'])
        self.policy_net = self.policy_net.to(self.device)
        self.policy_net.train()

        self.target_net.load_state_dict(checkpoint['policy_state_dict'])
        self.target_net = self.target_net.to(self.device)
        self.target_net.eval()

        self.optimizer = optim.Adam(self.policy_net.parameters())
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])

        if load_memory:
            memory = torch.load(mem_filename)
            self.memory = memory['memory']
