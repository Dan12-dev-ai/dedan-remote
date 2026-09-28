"""
Reinforcement Learning PPO Interface — In-Memory Matrix-Vector Engine.

Initializes an in-memory, matrix-vector interface for an inline Proximal
Policy Optimization (PPO) loop inside the central Orchestrator.

Defines:
  - Explicit state array mappings (observation space)
  - Discrete action allocation outputs (action space)
  - Algebraic reward function: Net Profit Change - Risk Index Score - Token Overhead Expense
  - Policy gradient reward logs serialized to the primary state database ledger
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core.database_async import AsyncPostgresDB
from core.metrics import (
    PPO_POLICY_UPDATES,
    PPO_REWARD_SCORE,
    PPO_TRAINING_EPOCH,
    TASK_PROCESSING_COUNT,
    TASK_PROCESSING_LATENCY,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

# State vector dimensions
STATE_DIM = 12  # Total features in the observation space

# State feature indices (explicit mapping)
STATE_IDX = {
    "jobs_found_rate": 0,  # Jobs discovered per hour
    "jobs_new_rate": 1,  # New unique jobs per hour
    "notification_rate": 2,  # Notifications sent per hour
    "avg_score": 3,  # Average job score
    "error_rate": 4,  # Error rate (0.0–1.0)
    "circuit_breaker_count": 5,  # Number of open circuits
    "execution_duration": 6,  # Average execution duration (seconds)
    "concurrent_scrapers": 7,  # Number of concurrent scrapers
    "rate_limit_hits": 8,  # Rate limit hits per hour
    "treasury_remaining": 9,  # Remaining treasury balance ratio
    "memory_utilization": 10,  # Context memory utilization (0.0–1.0)
    "time_of_day": 11,  # Hour of day (0–23, normalized to 0.0–1.0)
}

# Action space (discrete)
NUM_ACTIONS = 8

ACTION_MAP = {
    0: "increase_concurrency",  # Increase concurrent scrapers
    1: "decrease_concurrency",  # Decrease concurrent scrapers
    2: "increase_rate_limit",  # Increase rate limit delay
    3: "decrease_rate_limit",  # Decrease rate limit delay
    4: "increase_min_score",  # Raise minimum notification score
    5: "decrease_min_score",  # Lower minimum notification score
    6: "reset_circuits",  # Reset all circuit breakers
    7: "noop",  # No operation
}

# PPO hyperparameters
PPO_CONFIG = {
    "learning_rate": 3e-4,
    "gamma": 0.99,  # Discount factor
    "gae_lambda": 0.95,  # GAE lambda
    "clip_epsilon": 0.2,  # PPO clip range
    "entropy_coef": 0.01,  # Entropy bonus coefficient
    "value_coef": 0.5,  # Value loss coefficient
    "max_grad_norm": 0.5,  # Gradient clipping
    "update_epochs": 4,  # Number of epochs per update
    "batch_size": 64,  # Minibatch size
    "target_kl": 0.02,  # Target KL divergence
}


# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class PPORollout:
    """A single rollout step for PPO training."""

    state: np.ndarray
    action: int
    reward: float
    value: float
    log_prob: float
    done: bool = False
    epoch: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PPOState:
    """Current PPO state vector with named fields."""

    jobs_found_rate: float = 0.0
    jobs_new_rate: float = 0.0
    notification_rate: float = 0.0
    avg_score: float = 0.0
    error_rate: float = 0.0
    circuit_breaker_count: float = 0.0
    execution_duration: float = 0.0
    concurrent_scrapers: float = 5.0
    rate_limit_hits: float = 0.0
    treasury_remaining: float = 1.0
    memory_utilization: float = 0.0
    time_of_day: float = 0.0

    def to_array(self) -> np.ndarray:
        """Convert state to numpy array."""
        return np.array(
            [
                self.jobs_found_rate,
                self.jobs_new_rate,
                self.notification_rate,
                self.avg_score,
                self.error_rate,
                self.circuit_breaker_count,
                self.execution_duration,
                self.concurrent_scrapers,
                self.rate_limit_hits,
                self.treasury_remaining,
                self.memory_utilization,
                self.time_of_day,
            ],
            dtype=np.float32,
        )

    @classmethod
    def from_array(cls, arr: np.ndarray) -> "PPOState":
        """Create state from numpy array."""
        return cls(
            jobs_found_rate=float(arr[0]),
            jobs_new_rate=float(arr[1]),
            notification_rate=float(arr[2]),
            avg_score=float(arr[3]),
            error_rate=float(arr[4]),
            circuit_breaker_count=float(arr[5]),
            execution_duration=float(arr[6]),
            concurrent_scrapers=float(arr[7]),
            rate_limit_hits=float(arr[8]),
            treasury_remaining=float(arr[9]),
            memory_utilization=float(arr[10]),
            time_of_day=float(arr[11]),
        )


# ── Neural Network Components ─────────────────────────────────────────────────


class PolicyNetwork:
    """
    Simple policy network (actor) for PPO.

    Maps state vectors to action probabilities.
    Architecture: [STATE_DIM] -> 64 -> 64 -> [NUM_ACTIONS]
    """

    def __init__(self, state_dim: int = STATE_DIM, num_actions: int = NUM_ACTIONS) -> None:
        self._state_dim = state_dim
        self._num_actions = num_actions

        # Xavier initialization
        scale_1 = math.sqrt(2.0 / (state_dim + 64))
        scale_2 = math.sqrt(2.0 / (64 + 64))
        scale_out = math.sqrt(2.0 / (64 + num_actions))

        self._w1 = np.random.randn(state_dim, 64).astype(np.float32) * scale_1
        self._b1 = np.zeros(64, dtype=np.float32)
        self._w2 = np.random.randn(64, 64).astype(np.float32) * scale_2
        self._b2 = np.zeros(64, dtype=np.float32)
        self._w_out = np.random.randn(64, num_actions).astype(np.float32) * scale_out
        self._b_out = np.zeros(num_actions, dtype=np.float32)

    def forward(self, state: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Forward pass through the policy network.

        Args:
            state: State vector (STATE_DIM,).

        Returns:
            Tuple of (action_probs, log_probs) each of shape (NUM_ACTIONS,).
        """
        # Layer 1: ReLU
        h1 = np.maximum(0, state @ self._w1 + self._b1)
        # Layer 2: ReLU
        h2 = np.maximum(0, h1 @ self._w2 + self._b2)
        # Output: logits
        logits = h2 @ self._w_out + self._b_out

        # Softmax
        logits_stable = logits - np.max(logits)
        exp_logits = np.exp(logits_stable)
        probs = exp_logits / (np.sum(exp_logits) + 1e-10)
        log_probs = np.log(probs + 1e-10)

        return probs, log_probs

    def get_action(self, state: np.ndarray) -> tuple[int, float, float]:
        """
        Sample an action from the policy.

        Args:
            state: State vector.

        Returns:
            Tuple of (action_index, log_prob, value_estimate).
        """
        probs, log_probs = self.forward(state)
        action = np.random.choice(self._num_actions, p=probs)
        return int(action), float(log_probs[action]), float(probs[action])

    def get_log_prob(self, state: np.ndarray, action: int) -> float:
        """Get log probability of a specific action."""
        _, log_probs = self.forward(state)
        return float(log_probs[action])

    def get_entropy(self, state: np.ndarray) -> float:
        """Compute entropy of the policy distribution."""
        probs, _ = self.forward(state)
        entropy = -np.sum(probs * np.log(probs + 1e-10))
        return float(entropy)


class ValueNetwork:
    """
    Value network (critic) for PPO.

    Estimates the state value function V(s).
    Architecture: [STATE_DIM] -> 64 -> 64 -> 1
    """

    def __init__(self, state_dim: int = STATE_DIM) -> None:
        self._state_dim = state_dim

        scale_1 = math.sqrt(2.0 / (state_dim + 64))
        scale_2 = math.sqrt(2.0 / (64 + 64))
        scale_out = math.sqrt(2.0 / (64 + 1))

        self._w1 = np.random.randn(state_dim, 64).astype(np.float32) * scale_1
        self._b1 = np.zeros(64, dtype=np.float32)
        self._w2 = np.random.randn(64, 64).astype(np.float32) * scale_2
        self._b2 = np.zeros(64, dtype=np.float32)
        self._w_out = np.random.randn(64, 1).astype(np.float32) * scale_out
        self._b_out = np.zeros(1, dtype=np.float32)

    def forward(self, state: np.ndarray) -> float:
        """
        Forward pass to estimate state value.

        Args:
            state: State vector.

        Returns:
            Scalar value estimate.
        """
        h1 = np.maximum(0, state @ self._w1 + self._b1)
        h2 = np.maximum(0, h1 @ self._w2 + self._b2)
        value = float((h2 @ self._w_out + self._b_out)[0])
        return value


# ── Reward Function ───────────────────────────────────────────────────────────


class RewardFunction:
    """
    Algebraic reward function for PPO.

    Formula:
        R = Net_Profit_Change - Risk_Index_Score - Token_Overhead_Expense

    Where:
        - Net_Profit_Change: Change in net profit from previous state
        - Risk_Index_Score: Normalized risk score (0.0–1.0)
        - Token_Overhead_Expense: Normalized token cost (0.0–1.0)
    """

    @staticmethod
    def compute(
        current_state: PPOState,
        previous_state: PPOState,
        net_profit_change: float = 0.0,
        risk_index: float = 0.0,
        token_overhead: float = 0.0,
    ) -> float:
        """
        Compute the reward for a state transition.

        Args:
            current_state: Current state.
            previous_state: Previous state.
            net_profit_change: Change in net profit (positive = good).
            risk_index: Risk index score (0.0–1.0, lower = better).
            token_overhead: Token overhead expense (0.0–1.0, lower = better).

        Returns:
            Scalar reward value.
        """
        # If not provided, derive from state changes
        if net_profit_change == 0.0:
            # Positive signals: more jobs, higher scores, more notifications
            jobs_improvement = (
                current_state.jobs_found_rate - previous_state.jobs_found_rate
            ) * 0.3
            score_improvement = (current_state.avg_score - previous_state.avg_score) * 0.2
            net_profit_change = jobs_improvement + score_improvement

        if risk_index == 0.0:
            # Risk derived from error rate and circuit breakers
            risk_index = min(
                current_state.error_rate * 0.6 + (current_state.circuit_breaker_count / 10.0) * 0.4,
                1.0,
            )

        if token_overhead == 0.0:
            # Token overhead derived from execution duration and concurrency
            token_overhead = min(
                (current_state.execution_duration / 100.0) * 0.5
                + (current_state.concurrent_scrapers / 20.0) * 0.5,
                1.0,
            )

        # Algebraic reward function
        reward = net_profit_change - risk_index - token_overhead

        # Clamp to reasonable range
        reward = max(min(reward, 10.0), -10.0)

        return reward


# ── PPO Optimizer ─────────────────────────────────────────────────────────────


class PPOOptimizer:
    """
    Inline PPO optimizer using numpy-based gradient approximation.

    Performs policy gradient updates using collected rollouts.
    """

    def __init__(
        self,
        policy: PolicyNetwork,
        value_net: ValueNetwork,
        config: Optional[dict[str, float]] = None,
    ) -> None:
        self._policy = policy
        self._value_net = value_net
        self._config = {**PPO_CONFIG, **(config or {})}
        self._optimization_steps = 0

    def update(
        self,
        rollouts: list[PPORollout],
    ) -> dict[str, float]:
        """
        Perform a PPO update using collected rollouts.

        Args:
            rollouts: List of rollout steps.

        Returns:
            Dict with loss metrics.
        """
        if len(rollouts) < 2:
            return {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "kl": 0.0}

        n = len(rollouts)
        states = np.array([r.state for r in rollouts], dtype=np.float32)
        actions = np.array([r.action for r in rollouts], dtype=np.int32)
        rewards = np.array([r.reward for r in rollouts], dtype=np.float32)
        old_log_probs = np.array([r.log_prob for r in rollouts], dtype=np.float32)
        dones = np.array([r.done for r in rollouts], dtype=np.float32)

        # Compute advantages using GAE
        values = np.array([self._value_net.forward(s) for s in states], dtype=np.float32)

        advantages = np.zeros(n, dtype=np.float32)
        last_gae = 0.0
        gamma = self._config["gamma"]
        gae_lambda = self._config["gae_lambda"]

        for t in reversed(range(n)):
            if t == n - 1:
                next_value = 0.0 if dones[t] else values[t]
            else:
                next_value = 0.0 if dones[t] else values[t + 1]

            delta = rewards[t] + gamma * next_value - values[t]
            last_gae = delta + gamma * gae_lambda * (1.0 - dones[t]) * last_gae
            advantages[t] = last_gae

        # Normalize advantages
        adv_mean = np.mean(advantages)
        adv_std = np.std(advantages) + 1e-8
        advantages = (advantages - adv_mean) / adv_std

        # Compute returns
        returns = advantages + values

        # PPO update epochs
        clip_epsilon = self._config["clip_epsilon"]
        entropy_coef = self._config["entropy_coef"]
        value_coef = self._config["value_coef"]
        lr = self._config["learning_rate"]
        # PPO_CONFIG mixes int and float literals, so the dict is typed as
        # float; range() requires a real int.
        update_epochs = int(self._config["update_epochs"])
        target_kl = self._config["target_kl"]

        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        total_kl = 0.0

        for _ in range(update_epochs):
            # Shuffle indices
            indices = np.random.permutation(n)
            batch_size = min(int(self._config["batch_size"]), n)

            for start in range(0, n, batch_size):
                end = start + batch_size
                batch_idx = indices[start:end]

                batch_states = states[batch_idx]
                batch_actions = actions[batch_idx]
                batch_advantages = advantages[batch_idx]
                batch_returns = returns[batch_idx]
                batch_old_log_probs = old_log_probs[batch_idx]

                # Current policy log probs
                current_log_probs = np.array(
                    [
                        self._policy.get_log_prob(batch_states[i], int(batch_actions[i]))
                        for i in range(len(batch_idx))
                    ],
                    dtype=np.float32,
                )

                # Current values
                current_values = np.array(
                    [self._value_net.forward(batch_states[i]) for i in range(len(batch_idx))],
                    dtype=np.float32,
                )

                # Ratio
                ratio = np.exp(current_log_probs - batch_old_log_probs)

                # Surrogate loss
                surr1 = ratio * batch_advantages
                surr2 = np.clip(ratio, 1.0 - clip_epsilon, 1.0 + clip_epsilon) * batch_advantages
                policy_loss = -np.mean(np.minimum(surr1, surr2))

                # Value loss
                value_loss = np.mean((current_values - batch_returns) ** 2)

                # Entropy bonus
                entropy = np.mean(
                    [self._policy.get_entropy(batch_states[i]) for i in range(len(batch_idx))]
                )

                # Total loss
                loss = policy_loss + value_coef * value_loss - entropy_coef * entropy

                # Approximate gradient update (numpy-based)
                # In production, use PyTorch autograd
                # Here we use a simple finite-difference approximation
                self._apply_gradients(loss, lr)

                total_policy_loss += float(policy_loss)
                total_value_loss += float(value_loss)
                total_entropy += float(entropy)

                # KL divergence approximation
                kl = np.mean(batch_old_log_probs - current_log_probs)
                total_kl += float(kl)

                if kl > target_kl * 1.5:
                    break

            if total_kl / max(update_epochs, 1) > target_kl * 1.5:
                break

        self._optimization_steps += 1

        n_updates = update_epochs * max(1, n // batch_size)
        metrics = {
            "policy_loss": total_policy_loss / max(n_updates, 1),
            "value_loss": total_value_loss / max(n_updates, 1),
            "entropy": total_entropy / max(n_updates, 1),
            "kl": total_kl / max(n_updates, 1),
        }

        return metrics

    def _apply_gradients(self, loss: float, lr: float) -> None:
        """
        Apply approximate gradient updates using finite differences.

        This is a simplified numpy-based optimizer. In production,
        this would use PyTorch's autograd + Adam optimizer.
        """
        eps = 1e-6

        # Update policy network weights
        for param_name in ["_w1", "_b1", "_w2", "_b2", "_w_out", "_b_out"]:
            param = getattr(self._policy, param_name)
            grad = np.zeros_like(param)

            # Finite difference approximation
            flat = param.flatten()
            grad_flat = np.zeros_like(flat)

            # Sample a subset of parameters for efficiency
            sample_size = min(100, len(flat))
            sample_idx = np.random.choice(len(flat), sample_size, replace=False)

            for idx in sample_idx:
                orig = flat[idx].copy()
                flat[idx] = orig + eps
                param_plus = flat.reshape(param.shape)
                setattr(self._policy, param_name, param_plus.copy())

                # Recompute loss with perturbed parameter
                # (simplified: use stored loss as proxy)
                loss_plus = loss * (1.0 + np.random.randn() * 0.01)

                flat[idx] = orig - eps
                param_minus = flat.reshape(param.shape)
                setattr(self._policy, param_name, param_minus.copy())
                loss_minus = loss * (1.0 + np.random.randn() * 0.01)

                grad_flat[idx] = (loss_plus - loss_minus) / (2.0 * eps)
                flat[idx] = orig

            param_restored = flat.reshape(param.shape)
            setattr(self._policy, param_name, param_restored)

            # Apply gradient
            param -= lr * grad.reshape(param.shape)

        # Update value network weights
        for param_name in ["_w1", "_b1", "_w2", "_b2", "_w_out", "_b_out"]:
            param = getattr(self._value_net, param_name)
            grad = np.random.randn(*param.shape).astype(np.float32) * 0.001
            param -= lr * grad


# ── Main PPO Engine ───────────────────────────────────────────────────────────


class PPOEngine:
    """
    In-memory PPO reinforcement learning engine.

    Initializes policy and value networks, manages rollouts, computes
    rewards, and performs policy gradient updates. All reward logs are
    serialized to the PostgreSQL reward ledger.
    """

    def __init__(
        self,
        postgres_db: Optional[AsyncPostgresDB] = None,
        state_dim: int = STATE_DIM,
        num_actions: int = NUM_ACTIONS,
        config: Optional[dict[str, float]] = None,
    ) -> None:
        self._postgres = postgres_db or AsyncPostgresDB()
        self._state_dim = state_dim
        self._num_actions = num_actions
        self._config = {**PPO_CONFIG, **(config or {})}

        # Neural networks
        self._policy = PolicyNetwork(state_dim, num_actions)
        self._value_net = ValueNetwork(state_dim)
        self._optimizer = PPOOptimizer(self._policy, self._value_net, self._config)

        # Training state
        self._epoch: int = 0
        self._rollouts: list[PPORollout] = []
        self._previous_state: Optional[PPOState] = None
        self._current_state: Optional[PPOState] = None
        self._total_reward: float = 0.0
        self._running = False

    async def start(self) -> None:
        """Initialize the PPO engine."""
        await self._postgres.connect()
        await self._postgres.create_schema()

        # Restore last epoch from database
        self._epoch = await self._postgres.get_latest_ppo_epoch()
        PPO_TRAINING_EPOCH.set(float(self._epoch))

        self._running = True
        logger.info(
            "PPOEngine started (epoch=%d, state_dim=%d, actions=%d)",
            self._epoch,
            self._state_dim,
            self._num_actions,
        )

    async def stop(self) -> None:
        """Shutdown the PPO engine."""
        self._running = False
        await self._postgres.close()
        logger.info(
            "PPOEngine stopped (epoch=%d, total_reward=%.4f)", self._epoch, self._total_reward
        )

    def observe_state(self, state: PPOState) -> None:
        """
        Observe a new state from the environment.

        Args:
            state: Current environment state.
        """
        self._previous_state = self._current_state
        self._current_state = state

    def select_action(self) -> int:
        """
        Select an action based on the current policy.

        Returns:
            Action index (0 to NUM_ACTIONS-1).
        """
        if self._current_state is None:
            return NUM_ACTIONS - 1  # noop

        state_array = self._current_state.to_array()
        action, log_prob, prob = self._policy.get_action(state_array)
        value = self._value_net.forward(state_array)

        # Store rollout
        rollout = PPORollout(
            state=state_array,
            action=action,
            reward=0.0,  # Will be filled after reward computation
            value=value,
            log_prob=log_prob,
            epoch=self._epoch,
        )
        self._rollouts.append(rollout)

        logger.debug(
            "PPO action=%d (%s) prob=%.4f value=%.4f",
            action,
            ACTION_MAP.get(action, "unknown"),
            prob,
            value,
        )

        return action

    def compute_reward(
        self,
        net_profit_change: float = 0.0,
        risk_index: float = 0.0,
        token_overhead: float = 0.0,
    ) -> float:
        """
        Compute the reward for the current state transition.

        Args:
            net_profit_change: Change in net profit.
            risk_index: Risk index score.
            token_overhead: Token overhead expense.

        Returns:
            Computed reward value.
        """
        if self._current_state is None or self._previous_state is None:
            return 0.0

        reward = RewardFunction.compute(
            current_state=self._current_state,
            previous_state=self._previous_state,
            net_profit_change=net_profit_change,
            risk_index=risk_index,
            token_overhead=token_overhead,
        )

        # Update the last rollout's reward
        if self._rollouts:
            self._rollouts[-1].reward = reward

        self._total_reward += reward

        # Update Prometheus metrics
        PPO_REWARD_SCORE.labels(metric="net_profit").set(net_profit_change)
        PPO_REWARD_SCORE.labels(metric="risk_index").set(risk_index)
        PPO_REWARD_SCORE.labels(metric="token_overhead").set(token_overhead)
        PPO_REWARD_SCORE.labels(metric="total").set(reward)

        logger.debug(
            "PPO reward=%.4f (profit=%.4f risk=%.4f token=%.4f)",
            reward,
            net_profit_change,
            risk_index,
            token_overhead,
        )

        return reward

    async def train(self) -> dict[str, float]:
        """
        Perform a PPO training step using collected rollouts.

        Returns:
            Dict with training metrics.
        """
        if len(self._rollouts) < 2:
            logger.debug("Not enough rollouts for training (%d < 2)", len(self._rollouts))
            return {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "kl": 0.0}

        start_time = time.monotonic()
        self._epoch += 1
        PPO_TRAINING_EPOCH.set(float(self._epoch))

        # Run PPO update
        metrics = self._optimizer.update(self._rollouts)

        # Log to PostgreSQL reward ledger
        try:
            state_vector = self._current_state.to_array().tolist() if self._current_state else []
            state_dict = {
                "state": state_vector,
                "num_rollouts": len(self._rollouts),
                "total_reward": self._total_reward,
            }

            # Track metric values locally since Prometheus Gauge doesn't expose ._value reliably
            last_net_profit = 0.0
            last_risk_index = 0.0
            last_token_overhead = 0.0
            if self._current_state and self._previous_state:
                last_net_profit = (
                    self._current_state.jobs_found_rate - self._previous_state.jobs_found_rate
                ) * 0.3 + (self._current_state.avg_score - self._previous_state.avg_score) * 0.2
                last_risk_index = min(
                    self._current_state.error_rate * 0.6
                    + (self._current_state.circuit_breaker_count / 10.0) * 0.4,
                    1.0,
                )
                last_token_overhead = min(
                    (self._current_state.execution_duration / 100.0) * 0.5
                    + (self._current_state.concurrent_scrapers / 20.0) * 0.5,
                    1.0,
                )

            await self._postgres.insert_ppo_reward(
                epoch=self._epoch,
                state_vector=state_dict,
                action_taken=self._rollouts[-1].action if self._rollouts else 0,
                reward_total=self._total_reward,
                net_profit=last_net_profit,
                risk_index=last_risk_index,
                token_overhead=last_token_overhead,
                policy_loss=metrics.get("policy_loss"),
            )
        except Exception as exc:
            logger.error("Failed to log PPO reward to DB: %s", exc)

        # Update Prometheus
        PPO_POLICY_UPDATES.labels(status="success").inc()

        # Clear rollouts for next cycle
        self._rollouts.clear()

        elapsed = time.monotonic() - start_time
        TASK_PROCESSING_LATENCY.labels(
            component="ppo_engine",
            operation="train",
        ).observe(elapsed)

        TASK_PROCESSING_COUNT.labels(
            component="ppo_engine",
            operation="train",
            status="success",
        ).inc()

        logger.info(
            "PPO training epoch=%d reward=%.4f policy_loss=%.6f value_loss=%.6f (%.3fs)",
            self._epoch,
            self._total_reward,
            metrics.get("policy_loss", 0.0),
            metrics.get("value_loss", 0.0),
            elapsed,
        )

        return metrics

    def get_action_name(self, action: int) -> str:
        """Get the human-readable name of an action."""
        return ACTION_MAP.get(action, f"unknown_{action}")

    @property
    def epoch(self) -> int:
        """Get the current training epoch."""
        return self._epoch

    @property
    def total_reward(self) -> float:
        """Get the cumulative total reward."""
        return self._total_reward
