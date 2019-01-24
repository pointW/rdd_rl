import time
from copy import copy

import numpy as np

from vrep_arm_toolkit.simulation import vrep
from vrep_arm_toolkit.robots.ur5 import UR5
from vrep_arm_toolkit.grippers.rdd import RDD
import vrep_arm_toolkit.utils.vrep_utils as utils
from vrep_arm_toolkit.utils import transformations

import rospy
from std_msgs.msg import Float32MultiArray

VREP_BLOCKING = vrep.simx_opmode_blocking


class ScoopEnv:
    RIGHT = 0
    LEFT = 1
    CLOSE = 2
    OPEN = 3

    def __init__(self, port=19997, memory_size=60):
        rospy.init_node('env', anonymous=True)

        self.sim_client = utils.connectToSimulation('127.0.0.1', port)

        # Create UR5 and restart simulator
        self.rdd = RDD(self.sim_client)
        self.ur5 = UR5(self.sim_client, self.rdd)
        self.nA = 4

        self.cube = None
        self.cube_start_position = [-0.2, 0.85, 0.025]
        self.cube_size = [0.1, 0.2, 0.04]

        self.open_position = 0.3

        self.narrow_position = None
        self.wide_position = None

        # self.rdd_position = [0 for _ in range(2 * memory_size)]
        # self.rdd_force = [0 for _ in range(2 * memory_size)]
        self.narrow_p = []
        # self.narrow_t = [0 for _ in range(memory_size)]
        self.rdd_sub = rospy.Subscriber('sim/rdd_joints', Float32MultiArray, self.rddJointsCallback, queue_size=1)

        self.tip_position = None
        self.tip_orientation = None
        self.tip_pos_sub = rospy.Subscriber('sim/ur5_tip_pose', Float32MultiArray, self.tipPosCallback, queue_size=1)

        self.target_position = None
        self.target_orientation = None
        self.target_pos_sub = rospy.Subscriber('sim/ur5_target_pose', Float32MultiArray, self.targetPosCallback, queue_size=1)

        self.cube_position = None
        self.cube_orientation = None
        self.cube_pos_sub = rospy.Subscriber('sim/cube_pose', Float32MultiArray, self.cubePosCallback, queue_size=1)

    def rddJointsCallback(self, msg):
        """
        callback function for rdd joints state
        :param msg: Float32MultiArray
        :return:
        """
        data = list(msg.data)
        if len(self.narrow_p) < 1000:
            self.narrow_p.append(data[0])

        self.narrow_position = data[0]
        self.wide_position = data[1]

    def tipPosCallback(self, msg):
        """
        callback function for ur5 tip
        :param msg: Float32MultiArray
        :return:
        """
        data = list(msg.data)
        self.tip_position = data[:3]
        self.tip_orientation = data[3:]

    def targetPosCallback(self, msg):
        """
        callback function for ur5 target
        :param msg: Float32MultiArray
        :return:
        """
        data = list(msg.data)
        self.target_position = data[:3]
        self.target_orientation = data[3:]

    def cubePosCallback(self, msg):
        """
        callback function for cube
        :param msg: Float32MultiArray
        :return:
        """
        data = list(msg.data)
        self.cube_position = data[:3]
        self.cube_orientation = data[3:]

    def getObs(self):
        """
        get observation from position and force
        :return: the observation, List[List[float], List[float]]
        """
        p = copy(self.narrow_p)
        if len(p) == 0:
            p = [0.]
        xs = [i for i in range(len(p))]
        resampled = np.interp(np.linspace(0, len(p)-1, 20), xs, p).tolist()
        return resampled

    def reset(self):
        """
        reset the environment
        :return: the observation, List[List[float], List[float]]
        """
        vrep.simxStopSimulation(self.sim_client, VREP_BLOCKING)
        time.sleep(1)
        vrep.simxStartSimulation(self.sim_client, VREP_BLOCKING)
        time.sleep(1)

        sim_ret, self.cube = utils.getObjectHandle(self.sim_client, 'cube')

        utils.setObjectPosition(self.sim_client, self.ur5.UR5_target, [-0.2, 0.6, 0.08])

        dy = 0.3 * np.random.random()
        # dy = 0
        # dz = 0.1 * np.random.random() - 0.05
        current_pose = self.ur5.getEndEffectorPose()
        target_pose = current_pose.copy()
        target_pose[1, 3] += dy
        # target_pose[2, 3] += dz
        self.rdd.setFingerPos(-0.1)

        self.ur5.moveTo(target_pose)

        self.narrow_p = []
        self.target_position = None
        while self.target_position is None:
            time.sleep(0.1)
        return [0. for _ in range(20)]

    def step(self, a):
        """
        take a step
        :param a: action, int
        :return: observation, reward, done, info
        """
        self.narrow_p = []
        if a in [self.RIGHT, self.LEFT]:
            current_position = self.target_position
            target_pose = transformations.euler_matrix(self.target_orientation[0], self.target_orientation[1], self.target_orientation[2])
            target_pose[:3, -1] = current_position
            if a == self.RIGHT:
                target_pose[1, 3] -= 0.03
            elif a == self.LEFT:
                target_pose[1, 3] += 0.03
            self.ur5.moveTo(target_pose)
            # utils.setObjectPositionOneShot(self.sim_client, self.ur5.UR5_target, target_pose[:3, 3])

        elif a == self.CLOSE:
            self.rdd.setFingerPos(-0.1)
            _finger_pos = self.narrow_position
            time.sleep(0.1)
            t = 0
            while abs(_finger_pos - self.narrow_position) > 0.01:
                _finger_pos = self.narrow_position
                time.sleep(0.1)
                t += 1
                if t == 10:
                    break

        elif a == self.OPEN:
            self.rdd.setFingerPos()
            _finger_pos = self.narrow_position
            time.sleep(0.1)
            t = 0
            while abs(_finger_pos - self.narrow_position) > 0.01:
                _finger_pos = self.narrow_position
                time.sleep(0.1)
                t += 1
                if t == 10:
                    break

        cube_orientation = self.cube_orientation
        cube_position = self.cube_position
        tip_position = self.tip_position
        narrow_position = self.narrow_position
        target_position = self.target_position

        # arm is in wrong pose
        # sim_ret, target_position = utils.getObjectPosition(self.sim_client, self.ur5.UR5_target)
        if target_position[1] < 0.42 or target_position[1] > 0.95 or target_position[2] < 0 or target_position[
            2] > 0.2:
            print 'Wrong arm position: ', target_position
            return None, -1, True, None

        # cube in wrong position
        while any(np.isnan(cube_position)):
            res, cube_position = utils.getObjectPosition(self.sim_client, self.cube)
        if cube_position[0] < self.cube_start_position[0] - self.cube_size[0] or \
                cube_position[0] > self.cube_start_position[0] + self.cube_size[0] or \
                cube_position[1] < self.cube_start_position[1] - self.cube_size[1] or \
                cube_position[1] > self.cube_start_position[1] + self.cube_size[1]:
            print 'Wrong cube position: ', cube_position
            return None, 0, True, None

        # cube is lifted
        if np.all(tip_position > (np.array(cube_position) - np.array(self.cube_size))) and \
                np.all(tip_position < (np.array(cube_position) + np.array(self.cube_size))) and \
                cube_orientation[0] < -0.01 and \
                narrow_position > -0.5:
            return None, 1, True, None

        # cube is not lifted
        return self.getObs(), 0, False, None


if __name__ == '__main__':
    env = ScoopEnv(port=19997)
    env.reset()
    while True:
        a = input('input action')
        s_, r, done, info = env.step(int(a))
        print s_, r, done
        if done:
            break
