import time
from copy import copy
import thread
import numpy as np

from vrep_arm_toolkit.simulation import vrep
from vrep_arm_toolkit.robots.ur5 import UR5
from vrep_arm_toolkit.grippers.rdd import RDD
import vrep_arm_toolkit.utils.vrep_utils as utils
from vrep_arm_toolkit.utils import transformations

# import rospy
from std_msgs.msg import Float32MultiArray


class ScoopEnv:
    RIGHT = 0
    LEFT = 1
    UP = 2
    DOWN = 3

    def __init__(self, port=19997, memory_size=60):
        # rospy.init_node('env', anonymous=True)

        self.sim_client = utils.connectToSimulation('127.0.0.1', port)

        # Create UR5 and restart simulator
        self.rdd = RDD(self.sim_client)
        self.ur5 = UR5(self.sim_client, self.rdd)
        self.nA = 4

        self.cube = None
        self.cube_start_position = [-0.2, 0.85, 0.025]
        self.cube_size = [0.1, 0.2, 0.04]

        self.open_position = 0.3

        # self.narrow_position = None
        # self.tip_position = None
        # self.tip_orientation = None
        # self.target_position = None
        # self.target_orientation = None
        # self.cube_position = None
        # self.cube_orientation = None

        self.theta = []

        self.lock = thread.allocate_lock()

        self.running = False

        thread.start_new_thread(self.getSignalsFromSim, ())

    def getSignalsFromSim(self):
        while True:
            if not self.running:
                time.sleep(0.1)
                continue
            sim_ret, data = vrep.simxGetFloatSignal(self.sim_client, 'theta', vrep.simx_opmode_oneshot)
            if sim_ret == 0:
                self.lock.acquire(True)
                if len(self.theta) == 0:
                    self.theta.append(data)
                elif len(self.theta) < 1000 and data != self.theta[-1]:
                    self.theta.append(data)
                self.lock.release()
            # sim_ret, data = vrep.simxGetStringSignal(self.sim_client, 'tip_pos', vrep.simx_opmode_blocking)
            # if sim_ret == 0:
            #     data = vrep.simxUnpackFloats(data)
            #     self.tip_position = data[:3]
            #     self.tip_orientation = data[3:]
            #
            # sim_ret, data = vrep.simxGetStringSignal(self.sim_client, 'target_pos', vrep.simx_opmode_blocking)
            # if sim_ret == 0:
            #     data = vrep.simxUnpackFloats(data)
            #     self.target_position = data[:3]
            #     self.target_orientation = data[3:]
            #
            # sim_ret, data = vrep.simxGetStringSignal(self.sim_client, 'cube_pos', vrep.simx_opmode_blocking)
            # if sim_ret == 0:
            #     data = vrep.simxUnpackFloats(data)
            #     self.cube_position = data[:3]
            #     self.cube_orientation = data[3:]

    def getObs(self):
        p = copy(self.theta)
        if len(p) == 0:
            p = [0.]
        xs = [i for i in range(len(p))]
        resampled = np.interp(np.linspace(0, len(p) - 1, 20), xs, p).tolist()
        return resampled

    def reset(self):
        """
        reset the environment
        :return: the observation, List[List[float], List[float]]
        """
        self.running = False

        vrep.simxStopSimulation(self.sim_client, utils.VREP_BLOCKING)
        time.sleep(1)
        vrep.simxStartSimulation(self.sim_client, utils.VREP_BLOCKING)
        time.sleep(1)

        sim_ret, self.cube = utils.getObjectHandle(self.sim_client, 'cube')

        utils.setObjectPosition(self.sim_client, self.ur5.UR5_target, [-0.2, 0.6, 0.15])

        dy = 0.3 * np.random.random()
        dz = 0.1 * np.random.random() - 0.05
        current_pose = self.ur5.getEndEffectorPose()
        target_pose = current_pose.copy()
        target_pose[1, 3] += dy
        target_pose[2, 3] += dz
        self.rdd.setFingerPos()

        self.ur5.moveTo(target_pose)

        self.running = True

        self.lock.acquire(True)
        self.theta = []
        self.lock.release()
        time.sleep(0.5)
        return self.getObs()

    def step(self, a):
        """
        take a step
        :param a: action, int
        :return: observation, reward, done, info
        """
        self.lock.acquire()
        self.theta = []
        self.lock.release()

        sim_ret, current_position = utils.getObjectPosition(self.sim_client, self.ur5.UR5_target)
        sim_ret, current_orientation = utils.getObjectOrientation(self.sim_client, self.ur5.UR5_target)
        print 'current_position: ', current_position
        print 'current_orientation: ', current_orientation
        # current_position = copy(self.target_position)
        # current_orientation = copy(self.target_orientation)
        target_pose = transformations.euler_matrix(current_orientation[0], current_orientation[1], current_orientation[2])
        target_pose[:3, -1] = current_position
        if a == self.RIGHT:
            target_pose[1, 3] -= 0.03
        elif a == self.LEFT:
            target_pose[1, 3] += 0.03
        elif a == self.UP:
            target_pose[2, 3] += 0.03
        elif a == self.DOWN:
            target_pose[2, 3] -= 0.03
        self.ur5.moveTo(target_pose)
        # utils.setObjectPosition(self.sim_client, self.ur5.UR5_target, target_pose[:3, 3])

        sim_ret, cube_orientation = utils.getObjectOrientation(self.sim_client, self.cube)
        sim_ret, cube_position = utils.getObjectPosition(self.sim_client, self.cube)
        sim_ret, target_position = utils.getObjectPosition(self.sim_client, self.ur5.UR5_target)

        # cube_orientation = copy(self.cube_orientation)
        # cube_position = copy(self.cube_position)
        # tip_position = copy(self.tip_position)
        # narrow_position = copy(self.narrow_position)
        # target_position = copy(self.target_position)

        # arm is in wrong pose
        # sim_ret, target_position = utils.getObjectPosition(self.sim_client, self.ur5.UR5_target)
        if target_position[1] < 0.42 or target_position[1] > 0.95 or target_position[2] < 0 or target_position[
            2] > 0.2:
            print 'Wrong arm position: ', target_position
            return None, -1, True, None

        # cube in wrong position
        while any(np.isnan(cube_position)):
            sim_ret, cube_position = utils.getObjectPosition(self.sim_client, self.cube)
        if cube_position[0] < self.cube_start_position[0] - self.cube_size[0] or \
                cube_position[0] > self.cube_start_position[0] + self.cube_size[0] or \
                cube_position[1] < self.cube_start_position[1] - self.cube_size[1] or \
                cube_position[1] > self.cube_start_position[1] + self.cube_size[1]:
            print 'Wrong cube position: ', cube_position
            return None, 0, True, None

        # cube is lifted
        if cube_orientation[0] < -0.05:
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