"""
Bradley-Terry MAP ranking and adaptive pairwise selection.

Implements:
- MAP estimation of BT strengths with Gaussian prior (ridge/shrinkage)
- Proxy-uncertainty adaptive pair selection (exposure-only and top-heavy variants)
- Near-tie exploitation for opponent selection
- Full algorithm logging for audit trail
"""

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit  # logistic sigmoid
import json
import random
from datetime import datetime, timezone


class BradleyTerryMAP:
    """Bradley-Terry model with MAP estimation."""

    def __init__(self, sigma2=1.0):
        """
        Args:
            sigma2: Prior variance for Gaussian regularization. Higher = less shrinkage.
        """
        self.sigma2 = sigma2

    def fit(self, strategy_ids, comparisons):
        """
        Fit MAP Bradley-Terry model from pairwise comparison data.

        Args:
            strategy_ids: List of all strategy IDs (defines the index mapping).
            comparisons: List of (winner_id, loser_id) tuples.

        Returns:
            dict with:
                'strengths': {strategy_id: beta_value}
                'converged': bool
                'iterations': int
                'ranking': [(strategy_id, beta_value)] sorted descending
        """
        K = len(strategy_ids)
        if K < 2:
            return self._empty_result(strategy_ids)

        # Map IDs to 0-based indices
        id_to_idx = {sid: i for i, sid in enumerate(strategy_ids)}

        # Build win count matrix
        W = np.zeros((K, K))
        for winner, loser in comparisons:
            if winner in id_to_idx and loser in id_to_idx:
                i, j = id_to_idx[winner], id_to_idx[loser]
                W[i, j] += 1

        # If no valid comparisons, return zeros
        if W.sum() == 0:
            return self._empty_result(strategy_ids)

        # Fix beta[0] = 0 for identification; optimize beta[1:K]
        def neg_log_posterior(beta_free):
            beta = np.zeros(K)
            beta[1:] = beta_free

            # Log-likelihood
            ll = 0.0
            for i in range(K):
                for j in range(K):
                    if W[i, j] > 0:
                        ll += W[i, j] * (beta[i] - np.logaddexp(beta[i], beta[j]))

            # Gaussian prior: -1/(2*sigma2) * sum(beta_k^2)
            prior = -0.5 / self.sigma2 * np.sum(beta ** 2)

            # Minimize negative log posterior
            return -(ll + prior)

        # Gradient for faster optimization
        def gradient(beta_free):
            beta = np.zeros(K)
            beta[1:] = beta_free
            grad_full = np.zeros(K)

            for i in range(K):
                for j in range(K):
                    if W[i, j] > 0 or W[j, i] > 0:
                        pij = expit(beta[i] - beta[j])
                        if W[i, j] > 0:
                            grad_full[i] += W[i, j] * (1.0 - pij)
                            grad_full[j] -= W[i, j] * (1.0 - pij)

            # Prior gradient
            grad_full -= beta / self.sigma2

            return -grad_full[1:]  # Exclude fixed beta[0]

        # Optimize
        x0 = np.zeros(K - 1)
        result = minimize(neg_log_posterior, x0, jac=gradient, method='L-BFGS-B',
                          options={'maxiter': 500, 'ftol': 1e-10})

        beta = np.zeros(K)
        beta[1:] = result.x

        # Build result
        strengths = {strategy_ids[i]: float(beta[i]) for i in range(K)}
        ranking = sorted(strengths.items(), key=lambda x: x[1], reverse=True)

        return {
            'strengths': strengths,
            'converged': result.success,
            'iterations': result.nit,
            'ranking': ranking,
        }

    def _empty_result(self, strategy_ids):
        strengths = {sid: 0.0 for sid in strategy_ids}
        return {
            'strengths': strengths,
            'converged': True,
            'iterations': 0,
            'ranking': list(strengths.items()),
        }


class AdaptivePairSelector:
    """
    Selects pairs adaptively using proxy uncertainty + near-tie exploitation.
    """

    def __init__(self, sigma2=1.0, proxy='top_heavy'):
        """
        Args:
            sigma2: BT prior variance
            proxy: 'exposure' (Proxy A) or 'top_heavy' (Proxy B)
        """
        self.bt = BradleyTerryMAP(sigma2=sigma2)
        self.proxy = proxy

    def select_pair(self, strategy_ids, comparisons, exposure_counts):
        """
        Select the next pair to show.

        Args:
            strategy_ids: List of strategy IDs eligible for this level.
            comparisons: List of (winner_id, loser_id) from all past comparisons.
            exposure_counts: Dict {strategy_id: int} of how many times each was shown.

        Returns:
            dict with full selection details for logging.
        """
        K = len(strategy_ids)
        if K < 2:
            return None

        log = {
            'was_cold_start': False,
            'proxy_type': self.proxy,
            'focal_id': None,
            'focal_sampling_prob': None,
            'opponent_id': None,
            'opponent_strength_diff': None,
            'map_strengths': {},
            'exposure_counts': {sid: exposure_counts.get(sid, 0) for sid in strategy_ids},
            'uncertainty_weights': {},
            'map_converged': None,
            'map_iterations': None,
            'num_comparisons': len(comparisons),
        }

        # Cold start: no comparisons yet → random pair
        if len(comparisons) == 0:
            pair = random.sample(strategy_ids, 2)
            log['was_cold_start'] = True
            log['focal_id'] = pair[0]
            log['opponent_id'] = pair[1]
            return self._finalize(pair[0], pair[1], log)

        # Step 1: Refit MAP
        fit_result = self.bt.fit(strategy_ids, comparisons)
        beta = fit_result['strengths']
        log['map_strengths'] = {str(k): round(v, 6) for k, v in beta.items()}
        log['map_converged'] = fit_result['converged']
        log['map_iterations'] = fit_result['iterations']

        # Step 2: Compute proxy uncertainty weights
        weights = {}
        for sid in strategy_ids:
            e = exposure_counts.get(sid, 0)
            unc = 1.0 / np.sqrt(e + 1)

            if self.proxy == 'top_heavy':
                # Sigmoid of strength × exposure uncertainty
                s = expit(beta.get(sid, 0.0))
                weights[sid] = s * unc
            else:
                # Pure exposure uncertainty
                weights[sid] = unc

        log['uncertainty_weights'] = {str(k): round(v, 6) for k, v in weights.items()}

        # Normalize to probability distribution
        total_weight = sum(weights.values())
        if total_weight == 0:
            total_weight = 1.0
        probs = {sid: w / total_weight for sid, w in weights.items()}

        # Step 3: Sample focal item
        ids_list = list(probs.keys())
        prob_list = [probs[sid] for sid in ids_list]
        focal_idx = np.random.choice(len(ids_list), p=prob_list)
        focal_id = ids_list[focal_idx]
        log['focal_id'] = focal_id
        log['focal_sampling_prob'] = round(prob_list[focal_idx], 6)

        # Step 4: Exploitation — nearest strength neighbor with diversity
        focal_beta = beta.get(focal_id, 0.0)

        # Collect all candidates with their strength differences
        candidates = []
        for sid in strategy_ids:
            if sid == focal_id:
                continue
            diff = abs(beta.get(sid, 0.0) - focal_beta)
            candidates.append((sid, diff))

        candidates.sort(key=lambda x: x[1])

        # Check if we're in sparse-data phase: most strengths near-identical
        # (top near-tie diff < 0.01 means model hasn't differentiated yet)
        SPARSE_THRESHOLD = 0.01
        near_ties = [c for c in candidates if c[1] < SPARSE_THRESHOLD]

        if len(near_ties) > 1:
            # Sparse phase: among near-ties, prefer least-exposed for diversity
            near_tie_ids = [c[0] for c in near_ties]
            near_tie_exposures = [(sid, exposure_counts.get(sid, 0)) for sid in near_tie_ids]
            min_exp = min(e for _, e in near_tie_exposures)
            least_exposed = [sid for sid, e in near_tie_exposures if e <= min_exp + 1]
            best_opponent = random.choice(least_exposed)
            best_diff = abs(beta.get(best_opponent, 0.0) - focal_beta)
        else:
            # Normal phase: strict nearest-neighbor
            best_opponent = candidates[0][0]
            best_diff = candidates[0][1]

        log['opponent_id'] = best_opponent
        log['opponent_strength_diff'] = round(best_diff, 6)

        return self._finalize(focal_id, best_opponent, log)

    def _finalize(self, id_a, id_b, log):
        """Randomize left/right position to remove position bias."""
        if random.random() < 0.5:
            left, right = id_a, id_b
        else:
            left, right = id_b, id_a

        log['displayed_left_id'] = left
        log['displayed_right_id'] = right
        return log

    def compute_rankings(self, strategy_ids, comparisons):
        """
        Compute full rankings from all comparison data.

        Returns:
            dict with rankings and fit details.
        """
        if not comparisons:
            return {
                'rankings': [(sid, 0.0) for sid in strategy_ids],
                'converged': True,
                'iterations': 0,
            }

        fit_result = self.bt.fit(strategy_ids, comparisons)
        return {
            'rankings': fit_result['ranking'],
            'converged': fit_result['converged'],
            'iterations': fit_result['iterations'],
            'strengths': fit_result['strengths'],
        }


def get_bt_score(beta_value):
    """Convert raw BT strength to a 0-100 score using sigmoid."""
    return round(float(expit(beta_value)) * 100, 1)
