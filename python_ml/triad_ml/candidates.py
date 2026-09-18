"""Candidate model registry and leak-free preprocessing (FR-044, FR-046, PRD section 17.3).

Two rules the code enforces structurally rather than by convention:
  * preprocessing lives **inside** the pipeline, so imputation and scaling statistics can
    only ever be learned from the rows the pipeline is fitted on (FR-044);
  * only families in this registry may be trained; anything else is refused and logged
    (UC-08 exception flow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from sklearn.pipeline import Pipeline

DEFAULT_SEED = 42

# The dummy baseline is never removed: its PR-AUC is the churn rate, the "random" reference.
REGISTERED_FAMILIES = ("dummy", "logreg", "random_forest", "hist_gb", "xgboost", "mlp")


class UnregisteredFamily(ValueError):
    """Raised when a shortlist names a family outside the registry."""


@dataclass
class Candidate:
    family: str
    role: str
    params: dict[str, Any] = field(default_factory=dict)
    justification: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "role": self.role,
            "params": self.params,
            "justification": self.justification,
        }


def encoder():  # noqa: ANN201
    """Dense one-hot with rare-category folding, so unseen values map to 'infrequent'."""
    from sklearn.preprocessing import OneHotEncoder

    return OneHotEncoder(
        handle_unknown="infrequent_if_exist", min_frequency=20, sparse_output=False
    )


def preprocessing(num_cols: list[str], cat_cols: list[str], *, linear: bool):  # noqa: ANN201
    """Column transformer. Linear models get imputation + scaling; trees get passthrough."""
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if linear:
        num_pipe = Pipeline(
            [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
        )
    else:
        # Trees handle missing values natively; imputing would destroy that information.
        num_pipe = "passthrough"

    return ColumnTransformer(
        [("num", num_pipe, num_cols), ("cat", encoder(), cat_cols)],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_pipeline(
    candidate: Candidate,
    num_cols: list[str],
    cat_cols: list[str],
    *,
    pos_weight: float = 1.0,
    seed: int = DEFAULT_SEED,
) -> Pipeline:
    """One sklearn Pipeline per candidate: preprocessing + estimator, never separated."""
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import Pipeline

    family = candidate.family
    if family not in REGISTERED_FAMILIES:
        raise UnregisteredFamily(
            f"model family '{family}' is not in the algorithm registry "
            f"{list(REGISTERED_FAMILIES)}"
        )

    linear = family in ("logreg", "mlp")
    prep = preprocessing(num_cols, cat_cols, linear=linear)
    params = dict(candidate.params)

    # PRD 17.7: weighting distorts probabilities, so the MISCALIBRATED remedy chain may
    # rebuild a candidate with `balance_classes=False` (PRD 19.4, second remedy).
    balance = bool(params.pop("balance_classes", True))

    if family == "dummy":
        est = DummyClassifier(strategy="prior")
    elif family == "logreg":
        est = LogisticRegression(
            max_iter=params.pop("max_iter", 2000),
            C=params.pop("C", 1.0),
            class_weight="balanced" if balance else None,
            random_state=seed,
        )
    elif family == "random_forest":
        est = RandomForestClassifier(
            n_estimators=params.pop("n_estimators", 300),
            max_depth=params.pop("max_depth", 12),
            min_samples_leaf=params.pop("min_samples_leaf", 5),
            class_weight="balanced_subsample" if balance else None,
            n_jobs=params.pop("n_jobs", 2),
            random_state=seed,
        )
    elif family == "hist_gb":
        est = HistGradientBoostingClassifier(
            max_iter=params.pop("max_iter", 200),
            learning_rate=params.pop("learning_rate", 0.1),
            max_leaf_nodes=params.pop("max_leaf_nodes", 31),
            l2_regularization=params.pop("l2_regularization", 0.0),
            class_weight="balanced" if balance else None,
            random_state=seed,
        )
    elif family == "xgboost":
        from xgboost import XGBClassifier

        est = XGBClassifier(
            n_estimators=params.pop("n_estimators", 300),
            max_depth=params.pop("max_depth", 5),
            learning_rate=params.pop("learning_rate", 0.1),
            subsample=params.pop("subsample", 0.9),
            colsample_bytree=params.pop("colsample_bytree", 0.9),
            reg_lambda=params.pop("reg_lambda", 1.0),
            min_child_weight=params.pop("min_child_weight", 1.0),
            tree_method="hist",
            eval_metric="aucpr",
            scale_pos_weight=pos_weight if balance else 1.0,
            random_state=seed,
            n_jobs=params.pop("n_jobs", 2),
        )
    else:  # mlp
        est = MLPClassifier(
            hidden_layer_sizes=params.pop("hidden_layer_sizes", (64, 32)),
            max_iter=params.pop("max_iter", 300),
            random_state=seed,
        )

    return Pipeline([("prep", prep), ("clf", est)])


def shortlist(
    *,
    n_rows: int,
    n_features: int,
    has_high_dim_features: bool = False,
    interpretability_required: bool = False,
) -> list[Candidate]:
    """Rule-based shortlist (UC-08, PRD section 17.4). The agent may reorder with a
    justification, but it can never add a family that is not registered."""
    candidates = [
        Candidate("dummy", "floor", justification="PR-AUC floor equals the churn rate"),
        Candidate(
            "logreg",
            "parsimony_reference",
            justification="interpretable, well calibrated after scaling; the parsimony rule AP-2 "
            "compares every other family against it",
        ),
    ]
    if not interpretability_required:
        candidates += [
            Candidate("random_forest", "non_linear",
                      justification="robust to outliers and interactions"),
            Candidate("hist_gb", "non_linear",
                      justification="handles missing values natively; fast on wide tables"),
            Candidate("xgboost", "primary_non_linear",
                      justification="strong on tabular churn data and tunable"),
        ]
    # PRD 17.4: a neural network needs BOTH conditions, otherwise the record says why not.
    if n_rows >= 200_000 and has_high_dim_features:
        candidates.append(
            Candidate("mlp", "conditional",
                      justification=f"{n_rows} rows and high-dimensional features present")
        )
    return candidates


def validate_shortlist(families: list[str]) -> None:
    unknown = [f for f in families if f not in REGISTERED_FAMILIES]
    if unknown:
        raise UnregisteredFamily(f"unregistered model families refused: {unknown}")


def split_feature_types(
    df, *, label_col: str = "churned_90d", id_cols: tuple[str, ...] = ("customer_id",)
) -> tuple[list[str], list[str]]:
    """Numeric and categorical feature columns. Identifiers are never features (rule H2)."""
    import pandas as pd

    excluded = {label_col, "snapshot_date", *id_cols}
    num_cols, cat_cols = [], []
    for col in df.columns:
        if col in excluded:
            continue
        if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            num_cols.append(col)
        else:
            cat_cols.append(col)
    return num_cols, cat_cols
