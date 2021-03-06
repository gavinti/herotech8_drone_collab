import configparser
import random
import glob
from enum import IntEnum
from typing import Tuple, Dict, Optional

import gym
import matplotlib.colors as colors
import matplotlib.pyplot as plt
import numpy as np
from gym import spaces
from gym.utils import seeding

from grid_generators.random_maze import random_maze
from grid_generators.random_shape_maze import random_shape_maze
from grid_generators.random_start_goal import random_start_goal, random_starts_goals, random_starts_goals_in_subsquare
from rendering import fill_coords, point_in_rect, highlight_img, downsample
from matplotlib.patches import Circle

from copy import deepcopy
import torch


class WorldObj:  # not used yet
    """
    Base class for grid world objects
    """

    def __init__(self):
        self.pos = None
        self._observable = True

    @property
    def observable(self):
        return self._observable

    def encode(self) -> Tuple[int, ...]:
        """Encode the description of this object"""
        raise NotImplementedError

    def on_entering(self, agent) -> ():
        """Action to perform when an agent enter this object"""
        raise NotImplementedError

    def on_leaving(self, agent) -> ():
        """Action to perform when an agent exit this object"""
        raise NotImplementedError

    def can_overlap(self) -> bool:
        """Can an agent overlap this object?"""
        return True

    def render(self, r) -> ():
        """Draw this object with the given renderer"""
        raise NotImplementedError


class Grid:  # not used yet
    """
    Base class for grids and operations on it (not used yet)
    """
    # Type hints
    _obj_2_idx: Dict[Optional[WorldObj], int]

    # Static cache of pre-rendered tiles
    tile_cache = {}

    class EncodingError(Exception):
        """Exception raised for missing entry in _obj_2_idx"""
        pass

    def __init__(self, width: int, height: int):
        """Create an empty Grid"""
        self.width = width
        self.height = height

        self.grid = np.empty(shape=(width, height), dtype=WorldObj)

        self._idx_2_obj = {v: k for k, v in self._obj_2_idx.items()}

    @classmethod
    def from_array(cls, array: np.ndarray):
        (width, height) = array.shape
        out = cls(width, height)
        out.grid = array
        return out

    @property
    def obj_2_idx(self):
        return self._obj_2_idx

    def __contains__(self, item):
        return item in self.grid

    def __eq__(self, other):
        grid1 = self.encode()
        grid2 = other.encode()
        return np.array_equal(grid1, grid2)

    def __getitem__(self, item):
        out = self.grid.__getitem__(item)
        if isinstance(out, WorldObj):
            return out
        else:
            # slice
            return Grid.from_array(out)

    def __setitem__(self, key, value):
        if isinstance(value, Grid):
            self.grid.__setitem__(key, value.grid)
        else:
            self.grid.__setitem__(key, value)

    def set(self, i, j, v):
        """Set an element of the grid"""
        assert 0 <= i < self.width, "i index out of bounds"
        assert 0 <= j < self.height, "j index out of bounds"
        self.grid[i, j] = v

    def get(self, i, j):
        """Get an element of the grid"""
        assert 0 <= i < self.width, "i index out of bounds"
        assert 0 <= j < self.height, "j index out of bounds"
        return self.grid[i, j]

    def slice(self, top_x, top_y, width, height):
        """Get a subset of the grid"""
        assert 0 <= top_x < self.width
        assert 0 <= top_x + width < self.width
        assert 0 <= top_y + width < self.height
        assert 0 <= top_y < self.height

        return Grid.from_array(self.grid[top_x:(top_x + width), top_y:(top_y + height)])

    @classmethod
    def render_tile(cls, obj: WorldObj, highlight=False, tile_size=32, subdivs=3):
        """
        Render a tile and cache the result
        """

        # Hash map lookup key for the cache
        key = (highlight, tile_size)
        key = obj.encode() + key if obj else key

        if key in cls.tile_cache:
            return cls.tile_cache[key]

        img = np.zeros(shape=(tile_size * subdivs, tile_size * subdivs, 3), dtype=np.uint8)

        # Draw the grid lines (top and left edges)
        fill_coords(img, point_in_rect(0, 0.031, 0, 1), (100, 100, 100))
        fill_coords(img, point_in_rect(0, 1, 0, 0.031), (100, 100, 100))

        if obj is not None:
            obj.render(img)

        # Highlight the cell if needed
        if highlight:
            highlight_img(img)

        # Down-sample the image to perform super-sampling/anti-aliasing
        img = downsample(img, subdivs)

        # Cache the rendered tile
        cls.tile_cache[key] = img

        return img

    def render(self, tile_size=32, highlight_mask=None):
        """
        Render this grid at a given scale
        :param tile_size: tile size in pixels
        """

        if highlight_mask is None:
            highlight_mask = np.zeros(shape=(self.width, self.height), dtype=np.bool)

        # Compute the total grid size
        width_px = self.width * tile_size
        height_px = self.height * tile_size

        img = np.zeros(shape=(height_px, width_px, 3), dtype=np.uint8)

        # Render the grid
        for j in range(0, self.height):
            for i in range(0, self.width):
                cell = self.get(i, j)

                tile_img = Grid.render_tile(
                    cell,
                    highlight=highlight_mask[i, j],
                    tile_size=tile_size
                )

                ymin = j * tile_size
                ymax = (j + 1) * tile_size
                xmin = i * tile_size
                xmax = (i + 1) * tile_size
                img[ymin:ymax, xmin:xmax, :] = tile_img

        return img

    def encode(self, vis_mask: np.ndarray = None):
        """
        Produce a compact numpy encoding of the grid with tuples for each cells
        :param vis_mask: numpy array of boolean as a vision mask
        :return: numpy array
        """
        if vis_mask is None:
            vis_mask = np.ones((self.width, self.height), dtype=bool)

        assert vis_mask.shape == self.grid.shape

        array = np.zeros((self.width, self.height, 2), dtype="uint8")  # TODO: enable variable length encoding?

        for i in range(self.width):
            for j in range(self.height):
                if vis_mask[i, j]:
                    v = self.get(i, j)
                    if v is None:
                        try:
                            array[i, j, 0] = self._obj_2_idx[None], 0
                        except KeyError:
                            raise Grid.EncodingError("Empty grid cell encoding index not specified")
                    if v is not None:
                        if v.observable:
                            try:
                                array[i, j, 0] = self._obj_2_idx[None], 0
                            except KeyError:
                                raise Grid.EncodingError("Empty grid cell encoding index not specified for "
                                                         "unobservable object")
                        else:
                            try:
                                array[i, j, 0] = self._obj_2_idx[v.__class__]
                            except KeyError:
                                raise Grid.EncodingError(f"Grid cell encoding index for {v.__class__} not specified")
                            array[i, j, :] = v.encode()

        return array

    @classmethod
    def decode(cls, array):
        """
        Decode an array grid encoding back into a grid using this grid encoding
        :param array: an array grid encoded
        :return: grid
        """

        width, height, channels = array.shape
        assert channels == 2  # TODO: enable variable length encoding?

        grid = cls(width, height)

        for i in range(width):
            for j in range(height):
                type_idx, arg = array[i, j]
                # TODO : continue

class GridworldAgent:
    BATTERY = 150

    def __init__(self, agent_id, start, goal):
        self.id = agent_id

        # Position of the agent
        self.init_pos = start
        self.pos = start

        # Position of its goal
        self.init_goal = goal
        self.goal = goal

        # Battery
        self.battery = GridworldAgent.BATTERY  # percent

        # Boolean to know if the agent is done
        self.done = False


class GridWorld(gym.Env):
    """
    2D Grid world environment
    """

    metadata = {'render.modes': ['human']}

    class LegalActions(IntEnum):
        """ legal actions"""
        left = 0
        right = 1
        up = 2
        down = 3

    class GridLegend(IntEnum):
        # FREE = 0
        OBSTACLE = 1
        GCS = 2  # ground charging stations
        AGENT = 3
        GOAL = 4
        # OUT_OF_BOUNDS = 6  # Commented out for now since it interferes with the conv-net input tensor

    class UnknownAction(Exception):
        """Raised when an agent try to do an unknown action"""
        pass

    def __init__(self, n_agents=1, grid=np.ones((5, 5)), partial_obs=False, width=5, height=5,
                 col_wind=None, range_random_wind=0, probabilities=None):

        self.agents = [GridworldAgent(i, None, None) for i in range(n_agents)]
        self.grid = grid
        self.gcs = np.where(grid == GridWorld.GridLegend.GCS) # Ground charging station

        if probabilities is None and range_random_wind == 0:
            probabilities = [1]  # Zero noise

        # Define if the agents use partial observation or global observation
        # partial obs broken, don't use it
        self.partial_obs = partial_obs
        if self.partial_obs:
            self.agent_view_width = width
            self.agent_view_height = height

        self.actions = GridWorld.LegalActions
        self.action_space = spaces.Discrete(len(self.actions))

        self.observation_space = spaces.Box(low=0, high=1,
                                            shape=(1, len(self.GridLegend) + (n_agents - 1) * 2, *grid.shape),   # (dim of encoding, dim of one observation + partial obs of all the other agents, grid width, grid height)
                                            dtype='uint8')

        self.agents_initial_pos = [agent.pos for agent in self.agents]  # starting position of the agents on the grid

        # Wind effects -- TODO: May need to be moved into the world object? or is it okay here?
        self.np_random, _ = self.seed()  # Seeding the random number generator

        if col_wind is None:
            col_wind = np.zeros((len(self.grid, )))

        self.col_wind = col_wind  # Static wind  (rightwards is positive)

        self.range_random_wind = range_random_wind  # Random (dynamic) wind added on top of static wind
        self.w_range = np.arange(-self.range_random_wind, self.range_random_wind + 1)
        self.probabilities = probabilities  # Stochasticity implemented through noise
        assert sum(self.probabilities) == 1

        self.step_count = 0
        self.max_steps = 100

        self.rewards = {"free": -0.04,
                        "obstacles": -0.75,
                        "goal": 10.0,
                        "out_of_bounds": -0.8,
                        "battery_depleted": -10.0}

    def reset(self, reset_starts_goals=True, radius=10, reset_grid=True):
        if reset_grid:
            _grid = self._gen_grid(*self.grid.shape)
            gcs = (random.randrange(0, self.grid.shape[0]), random.randrange(0, self.grid.shape[1]))

            # following loop is potentially infinite (but then you are very unlucky)
            while (_grid[gcs]  # if the gcs is on one of the generated obstacle
                  or (not reset_starts_goals  # if we will reset starts and goal no need to go further
                      and (any(_grid[agent.init_pos]  # if any of the generated obstacles is on one of the start position
                              or _grid[agent.init_goal]  # or one of the goal position 
                              or abs(gcs[0] - agent.init_pos[0]) + abs(gcs[1] - agent.init_pos[1]) > 10  # or the generated gcs is out of range from the starting position
                              or abs(gcs[0] - agent.init_goal[0]) + abs(gcs[1] - agent.init_goal[1]) > 10  # or the goal is out of range from the gcs
                              for agent in self.agents)))):
                # generate new obstacles and gcs
                _grid = self._gen_grid(*self.grid.shape)
                gcs = (random.randrange(0, self.grid.shape[0]), random.randrange(0, self.grid.shape[1]))

            # if not reset_starts_goals:
            #     starts, goals = (agent.init_pos for agent in self.agents), (agent.init_goal for agent in self.agents)

            #     tries = 0  # just to protect from the very unlikely case of an infinite loop
            #     while tries < 100 and any(_grid[start] for start in starts) or any(_grid[goal] for goal in goals):
            #         # if any of the generated obstacles is on one of the goal or start positions :
            #         # generate new obstacles
            #         _grid = self._gen_grid(*self.grid.shape)
            #     if tries == 100:
            #         _grid = np.zeros(self.grid.shape)
            
            _grid[gcs] = self.GridLegend.GCS
            self.grid = _grid
            self.gcs = gcs
            print(f"New grid generated, gcs is in {gcs}")

        
        
        if reset_starts_goals:
            starts, goals = self._gen_starts_goals_positions(radius)

            # following loop is potentially infinite 
            while (any(self.grid[start]  # if any of the start position is on an obstacle
                      or abs(self.gcs[0] - start[0]) + abs(self.gcs[1] - start[1]) > 10  # or out of range of the gcs
                      for start in starts)
                  or any(self.grid[goal]  # if any of the goal position is on an obstacle
                        or abs(self.gcs[0] - goal[0]) + abs(self.gcs[1] - goal[1]) > 10 # or out of range of the gcs
                        for goal in goals)):
                # generate new positions
                starts, goals = self._gen_starts_goals_positions(radius)

            # while (any(self.grid[start] for start in starts) or any(self.grid[goal] for goal in goals)):
            #     # if any of the generated position is on one of the obstacles : generate new positions
            #     starts, goals = self._gen_starts_goals_positions(radius)

            print(f"New starts are {starts} and new goals are {goals}")

            for i in range(len(self.agents)):
                agent = self.agents[i]
                agent.init_pos = starts[i]
                agent.init_goal = goals[i]

        for agent in self.agents:
            agent.pos = agent.init_pos
            agent.goal = agent.init_goal
            agent.done = False
            agent.battery = GridworldAgent.BATTERY

        # self.render()  # show the initial arrangement of the grid

        # return first observation
        return self.gen_obs()

    def _gen_grid(self, width, height):
        """Generate a new grid"""

        # _grid = random_shape_maze(width, height, max_shapes=5, max_size=3, allow_overlap=False)
        # _grid = np.genfromtxt(random.choice(glob.glob("sample_grid/*.csv")), delimiter=',')
        _grid = np.zeros(self.grid.shape)

        return _grid

    def _gen_starts_goals_positions(self, radius=10):
        """Generate new starts and goal positions for the agents of the environment"""

        # # first row / last row ?
        # start_bounds = ((0, 1), (0, self.grid.shape[0]))
        # goal_bounds = ((self.grid.shape[0] - 1, self.grid.shape[0]), (0, self.grid.shape[0]))
        # starts, goals = random_starts_goals(n=len(self.agents), width=self.grid.shape[0],
        #                                     start_bounds=start_bounds, goal_bounds=goal_bounds)

        # whole grid ?
        start_bounds = ((0, self.grid.shape[1]), (0, self.grid.shape[0]))
        goal_bounds = ((0, self.grid.shape[1]), (0, self.grid.shape[0]))
        starts, goals = random_starts_goals(n=len(self.agents), width=self.grid.shape[0],
                                            start_bounds=start_bounds, goal_bounds=goal_bounds)

        # # within a sub_grid ?
        # starts, goals = random_starts_goals_in_subsquare(n=len(self.agents), width=self.grid.shape[0], sub_width=radius)

        # # goal around gcs ?
        # if radius >= self.grid.shape[0]:
        #     start_bounds = ((0, self.grid.shape[1]), (0, self.grid.shape[0]))
        #     goal_bounds = ((0, self.grid.shape[1]), (0, self.grid.shape[0]))
        # else:
        #     x,y = self.gcs[0], self.gcs[1]
        #     top_x = x-radius+1 if x-radius+1>=0 else 0
        #     top_y = y-radius+1 if y-radius+1>=0 else 0
        #     bottom_x = x+radius if x+radius-1<self.grid.shape[0] else self.grid.shape[0]
        #     bottom_y = y+radius if y+radius-1<self.grid.shape[1] else self.grid.shape[1]

        #     start_bounds = ((0, self.grid.shape[1]), (0, self.grid.shape[0]))
        #     goal_bounds = ((top_x, bottom_x), (top_y, bottom_y))
        
        # starts, goals = random_starts_goals(n=len(self.agents), width=self.grid.shape[0],
        #                                     start_bounds=start_bounds, goal_bounds=goal_bounds)
        
        return starts, goals

    def trans_function(self, state, action, noise):
        """Creating transition function based on environmental factors
        For now, only wind considered -> static + random (pre-defined probabilities that the agent can
        figure out through experience)"""

        n, m = state

        if self.col_wind[n] != 0:
            wind = self.col_wind[n] + noise

        else:
            wind = 0  # Irrespective of random noise

        # Go UP
        if action == self.actions.up:
            (n, m) = (n - 1, m + wind)

        # Go DOWN
        elif action == self.actions.down:
            (n, m) = (n + 1, m + wind)

        # Go LEFT
        elif action == self.actions.left:
            (n, m) = (n, m - 1 + wind)

        # Go RIGHT
        elif action == self.actions.right:
            (n, m) = (n, m + 1 + wind)

        return n, m

    def _reward_agent(self, i):
        """ compute the reward for the i-th agent in the current state"""
        illegal = False
        done = False
        agent = self.agents[i]
        (n, m) = agent.pos

        # check for out of bounds
        if not (0 <= n < self.grid.shape[0] and 0 <= m < self.grid.shape[1]):
            reward = self.rewards["out_of_bounds"]
            # done = True
            illegal = True

        # check for collisions with obstacles (statics and dynamics)
        # for now it only checks for obstacles and others agents but it could be generalized with
        # the definition of Cell objects : check if cell is empty or not
        elif (self.grid[n, m] == self.GridLegend.OBSTACLE  # obstacles
              or (n, m) in [self.agents[j].pos for j in range(len(self.agents)) if j != i]):  # other agents
            reward = self.rewards["obstacles"]
            illegal = True
            # done = True

        # check if agent reached its goal
        elif (n, m) == agent.goal:
            reward = self.rewards["goal"]
            done = True

        # penalise the agent for extra moves
        else:
            reward = self.rewards["free"]

        if agent.battery <= 0:
            reward = self.rewards["battery_depleted"]
            done = True

        return reward, illegal, done

    def step(self, actions):
        self.step_count += 1

        assert len(actions) == len(self.agents), "number of actions must be equal to number of agents"

        # get a random permutation ( agents actions/reward must be order-independent)
        # random_order = np.random.permutation(len(self.agents))

        rewards = np.zeros(len(actions))

        # compute the moves
        moves = [agent.pos for agent in self.agents]

        for i in range(len(self.agents)):

            # agent mission is already done
            if self.agents[i].done:
                continue

            action = actions[i]
            agent = self.agents[i]

            # agent current pos
            (n, m) = agent.pos  # n is the row number, m is the column number

            # Adding random noise (wind) to the action
            noise = self.np_random.choice(self.w_range, p=self.probabilities)

            # Generate move
            (n_, m_) = self.trans_function((n, m), action, noise)

            # Store backup of move for each agent (i)
            moves[i] = (n_, m_)

        # compute rewards and apply moves if they are legal:
        # remember old positions
        old_pos = [agent.pos for agent in self.agents]

        # apply the moves (even if illegal)
        for i in range(len(self.agents)):
            # agent mission is already done
            if self.agents[i].done:
                continue

            self.agents[i].pos = moves[i]

            self.agents[i].battery -= 10

        # compute rewards and illegal assertions and cancel move if illegal
        illegals = [False for agent in self.agents]
        done = [agent.done for agent in self.agents]

        for i in range(len(self.agents)):
                # agent mission is already done
                if self.agents[i].done:
                    continue

                # compute rewards and illegal assertions
                rewards[i], illegals[i], done[i] = self._reward_agent(i)

        # recursively solve conflicts
        # now stop computing reward after first canceled move
        # we could also apply reward after the cancelling : 
        # it would keep good reward when no conflict and apply the "last" bad reward obtained during conflict resolution
        # even if the conflict happend after a canceled move and a return to old position (case when another agent took the old position)

        while any(illegals): # recursively solve conflicts
            # cancel moves if illegal
            for i in range(len(self.agents)):
                if illegals[i]:
                    self.agents[i].pos = old_pos[i]
                    illegals[i] = False

            # compute new illegal assertions in case of newly created conflict because of the return to an old position
            for i in range(len(self.agents)):
                # agent mission is already done or if its move was canceled (remove second condition to apply above suggested idea)
                if self.agents[i].done or self.agents[i].pos == old_pos[i]:
                    continue

                # compute rewards and illegal assertions
                rewards[i], illegals[i], done[i] = self._reward_agent(i)

        # handle specific cells events and apply done statements after
        for i in range(len(self.agents)):
            # agent mission is already done
            if self.agents[i].done:
                continue

            # if agent reachs a GCS, charge up the battery
            if self.grid[self.agents[i].pos] == self.GridLegend.GCS:
                self.agents[i].battery = GridworldAgent.BATTERY  # TODO : look into slow charging (need to introduce a new "stay idle" action) 

            self.agents[i].done = done[i]

        # game over if all the agents are done
        done = [agent.done for agent in self.agents]

        # compute observation
        obs = self.gen_obs()

        return obs, rewards, done, {}

    def gen_obs(self, tensor=1):
        """Generate the observation"""
        return [self._gen_obs_agent(agent, tensor) for agent in self.agents]

    def _gen_obs_agent(self, agent, tensor=1):
        """Generate the agent's view"""
        if self.partial_obs:
            x, y = agent.pos
            w, h = self.agent_view_width, self.agent_view_height
            sub_grid = np.full((w, h), self.GridLegend.OUT_OF_BOUNDS)

            # compute sub_grid corners
            top_x, top_y = x - w // 2, y - h // 2

            for j in range(0, h):
                for i in range(0, w):
                    n = top_x + i
                    m = top_y + j

                    if 0 <= n < self.grid.shape[0] and 0 <= m < self.grid.shape[1]:
                        sub_grid[i, j] = self.grid[n, m]

            return sub_grid

        else:
            canvas = self.grid.copy()

            # canvas[agent.pos] = self.GridLegend.AGENT  # TODO: add other agents in the local obs of single agents

            # # only mark the goal of the agent (not the ones of the others)
            # canvas[agent.goal] = self.GridLegend.GOAL

            # # Convert to len(dict)-dimensional tensor for Conv_SQN. Can turn on or off
            # if tensor == 1:
            #     canvas = self.grid2tensor(canvas)

            # return canvas

            key_grids = []

            for key in self.GridLegend:
                idx = np.where(canvas == key)
                key_grid = np.zeros(canvas.shape)
                key_grid[idx] = 1
                if key == self.GridLegend.AGENT:
                    key_grid[agent.pos] = agent.battery  # TODO: add other agents in the local obs of single agents
                elif key == self.GridLegend.GOAL:
                    key_grid[agent.goal] = 1  # only mark the goal of the agent (not the ones of the others)
                # elif key == self.GridLegend.GCS:
                #     continue
                key_grids.append(key_grid)

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            obs = torch.as_tensor(key_grids, device=device)
            obs = obs.reshape(1, len(self.GridLegend), canvas.shape[0], canvas.shape[1])

            return obs

    def eval_value_func(self, agent_id, model):
        """Function to evaluate the value of each position in the grid,
        given a unique agent ID (context for values)"""

        # Consider the value function for the selected action only
        agent = self.agents[agent_id]

        # Create grid template (only determines the size)
        grid = deepcopy(self.grid)  # Copy over current position of grid (immediately after reset)

        # Goal space is fixed
        grid[agent.goal] = self.GridLegend.GOAL

        # Backup for resetting the agent position
        grid_backup = deepcopy(grid)

        # Free spaces
        free_spaces = np.where(grid[:, :] == 1)  # returns are row and cols in a tuple ('1' is used for free cells)
        row = free_spaces[0]
        col = free_spaces[1]

        # Obstacle spaces
        obstacle_spaces = np.where(grid[:, :] == self.GridLegend.OBSTACLE)  # returns are row and cols in a tuple

        # Initialize the value function
        v_values = np.zeros_like(grid)  # Obstacles will have a large negative value

        # Move the agent to different positions on the grid and find the value function of that positions
        for ii in range(len(row)):
            n = row[ii]
            m = col[ii]

            grid[n, m] = self.GridLegend.AGENT  # Set agent current position

            canvas = grid.copy()

            observation = self.grid2tensor(canvas)

            with torch.no_grad():

                if model == 'SQN':
                    # Compute 'soft' Q values
                    q_values = agent.policy_model(observation)
                    # Compute soft-Value V value of the agent being in that position
                    value = agent.alpha * torch.logsumexp(q_values / agent.alpha, dim=1, keepdim=True)
                    v_values[n, m] = value.cpu().detach().numpy()

                elif model == 'DQN':
                    # Compute DQN Q-values -> Maximisation over the Q values at any state state (Bellman Eqn.)
                    v_values[n, m] = agent.policy_model(observation).max(1)[0]

            grid = deepcopy(grid_backup)  # Reset grid (erase agent current position)

        # Make sure the goal state is a sink (largest V value)
        v_values[agent.goal] = min(np.amax(v_values) + 0.5, np.amax(v_values) * 2)

        # Setting the obstacle V values to the lowest
        row = obstacle_spaces[0]
        col = obstacle_spaces[1]
        min_ = deepcopy(np.amin(v_values) - 0.5)

        for ii in range(len(row)):
            n = row[ii]
            m = col[ii]

            v_values[n, m] = min_

        # Shift all values to greater than 0
        min_ = np.amin(v_values)

        if min_ < 0:
            # Shift all values upwards by min_
            v_values -= min_

            # Reset the value of the goal state to prevent overpowering of value
            v_values[agent.goal] = 0
            v_values[agent.goal] = min(np.amax(v_values) + 0.5, np.amax(v_values) * 2)

        # Min-max scaling of V values (applicable only to all positive values)
        v_values = (v_values - np.amin(v_values)) / (np.amax(v_values) - np.amin(v_values))

        return v_values

    def greedy_det_policy(self, v_values, agent_id):
        """Given a value function for an agent, the purpose of this function
        is to create a deterministic policy from any position to continue mission"""

        (rows, cols) = np.shape(self.grid)

        # Initializing arrow vectors for the quiver plot
        u = np.zeros((rows, cols))
        v = np.zeros((rows, cols))

        for n in range(rows):
            for m in range(cols):

                # Do not consider positions where the obstacles/terminal state are
                if self.grid[n, m] == self.GridLegend.OBSTACLE or (n, m) == self.agents[agent_id].goal:
                    continue

                moves = {}

                # Check above
                if n > 0:
                    moves[self.LegalActions.up] = v_values[n - 1, m]
                else:
                    moves[self.LegalActions.up] = np.amin(v_values)

                # Check below
                if n < rows - 1:
                    moves[self.LegalActions.down] = v_values[n + 1, m]
                else:
                    moves[self.LegalActions.down] = np.amin(v_values)  # Equal to obstacles' V values

                # Check left
                if m > 0:
                    moves[self.LegalActions.left] = v_values[n, m - 1]
                else:
                    moves[self.LegalActions.left] = np.amin(v_values)

                # Check right
                if m < cols - 1:
                    moves[self.LegalActions.right] = v_values[n, m + 1]
                else:
                    moves[self.LegalActions.right] = np.amin(v_values)

                action = max(moves, key=moves.get)

                if action == self.LegalActions.up:
                    u[n, m] = 0
                    v[n, m] = 1

                elif action == self.LegalActions.down:
                    u[n, m] = 0
                    v[n, m] = -1

                elif action == self.LegalActions.right:
                    u[n, m] = 1
                    v[n, m] = 0

                elif action == self.LegalActions.left:
                    u[n, m] = -1
                    v[n, m] = 0

        return u, v

    def render(self, mode='human', value_func=False, v_values=np.zeros((5, 5)),
               policy=False, u=np.zeros((5, 5)), v=np.zeros((5, 5))):

        # With or without greedy deterministic policy
        if not value_func:

            canvas = self.grid.copy().astype(float)

            goals_canvas = np.zeros(self.grid.shape)
            masks = []
            for agent in self.agents:
                mask = np.ones(canvas.shape)
                # mark the terminal states in 0.9
                goals_canvas[agent.goal] = 0.7
                mask[agent.goal] = 0
                masks.append(mask)

            if mode == "human":
                plt.grid("on")

                cmaps = ['Purples', 'Greens', 'Oranges', 'Reds', 'Blues']

                ax = plt.gca()
                rows, cols = self.grid.shape
                ax.set_xticks(np.arange(0.5, rows, 1))
                ax.set_yticks(np.arange(0.5, cols, 1))
                ax.set_xticklabels([])
                ax.set_yticklabels([])

                canvas[self.gcs] = 0  # Don't want to draw those with imshow

                ax.imshow(canvas, interpolation='none', cmap='binary')

                from numpy.ma import masked_array
                for i in range(len(self.agents)):
                    im = masked_array(goals_canvas, masks[i])
                    ax.imshow(im, vmin=0, vmax=1, interpolation="none", cmap=cmaps[i])
                    ax.add_patch(Circle(self.agents[i].pos[::-1], radius=0.47, color=plt.get_cmap(cmaps[i])(0.7)))
                

                # draw a star in a cell 
                fig = plt.gcf()
                bbox = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
                cell_width = bbox.width*fig.dpi

                plt.scatter(*self.gcs[::-1],s=cell_width, marker="*", color="black")

            else:
                super(GridWorld, self).render(mode=mode)  # just raise an exception for not Implemented mode

        elif value_func:

            # v_values_sq = v_values ** 2  # Squaring to see more contrast between places

            v_min = np.amin(v_values)
            v_max = np.amax(v_values)

            plt.imshow(v_values, vmin=v_min, vmax=v_max, zorder=0)

            plt.colorbar()
            plt.yticks(np.arange(-0.5, np.shape(self.grid)[0] + 0.5, step=1))
            plt.xticks(np.arange(-0.5, np.shape(self.grid)[1] + 0.5, step=1))
            plt.grid()

            if not policy:
                plt.title("Value function map")

        if policy:
            plt.quiver(np.arange(np.shape(self.grid)[0]), np.arange(np.shape(self.grid)[1]), u, v, zorder=10,
                       label="Policy")
            plt.title('Equivalent Greedy Policy')

        # plt.show()

    def seed(self, seed=None):
        """Sets the seed for the environment to maintain consistency during training"""

        rn_gen, seed = seeding.np_random(seed)

        return rn_gen, seed

    def grid2tensor(self, grid):
        """Function to convert the observation into a n-dimensional grid according to the dict. size
         such that each grid has 1 at the position of the corresponding dict item. Needed for Conv_SQN"""
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        key_grids = []

        for key in self.GridLegend:
            idx = np.where(grid == key)
            key_grid = np.zeros(grid.shape)
            key_grid[idx] = 1
            key_grids.append(key_grid)

        obs = torch.as_tensor(key_grids, device=device)
        obs = obs.reshape(1, len(self.GridLegend), grid.shape[0], grid.shape[1])

        return obs

    # def save(self, directory, datetime):
    #     for agent in self.agents:
    #         agent.save(f"{directory}/{datetime}_{agent.id}.pt")

    def read_reward_config(self, config):
        grid_param = config["Gridworld Parameters"]
        self.rewards["free"] = grid_param.getfloat("FREE", self.rewards["free"])
        self.rewards["goal"] = grid_param.getfloat("GOAL", self.rewards["goal"])
        self.rewards["obstacles"] = grid_param.getfloat("OBSTACLE", self.rewards["obstacles"])
        self.rewards["out_of_bounds"] = grid_param.getfloat("OUT_OF_BOUNDS", self.rewards["out_of_bounds"])
        self.rewards["battery_depleted"] = grid_param.getfloat("BATTERY_DEPLETED", self.rewards["battery_depleted"])
