"""XGBoost baseline trainer for the train-tabular skill."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import pandas as pd


def _is_classification(y: pd.Series) -> bool:
    return (
        y.dtype == "object" or y.dtype.name.startswith("category") or y.nunique() <= 20
    )


def run(
    train_path: str,
    val_path: str,
    target: str,
    save_to: str,
    test_path: str | None = None,
    n_estimators: int = 600,
    early_stopping_rounds: int = 50,
) -> dict[str, Any]:
    """Train an XGBoost model and save to disk. Returns metrics."""
    import xgboost as xgb

    try:
        import trainable  # streaming logger injected by sandbox preamble
    except ImportError:
        trainable = None

    train = pd.read_parquet(train_path)
    val = pd.read_parquet(val_path)
    feature_cols = [c for c in train.columns if c != target]

    X_train, y_train = train[feature_cols], train[target]
    X_val, y_val = val[feature_cols], val[target]

    is_clf = _is_classification(y_train)
    if is_clf:
        n_classes = int(y_train.nunique())
        model = xgb.XGBClassifier(
            n_estimators=n_estimators,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="binary:logistic" if n_classes == 2 else "multi:softprob",
            eval_metric="auc" if n_classes == 2 else "mlogloss",
            tree_method="hist",
            early_stopping_rounds=early_stopping_rounds,
        )
    else:
        model = xgb.XGBRegressor(
            n_estimators=n_estimators,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method="hist",
            early_stopping_rounds=early_stopping_rounds,
        )

    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Stream a final epoch metric so the dashboard sees something
    if trainable:
        results = model.evals_result()
        for step, val in enumerate(
            list(results.get("validation_0", {}).values())[0] if results else []
        ):
            if step % 50 == 0:
                trainable.log(step, {"val_metric": float(val)})

    preds = model.predict(X_val)
    metrics: dict[str, Any] = {"val_n": len(y_val)}
    if is_clf:
        from sklearn.metrics import accuracy_score, f1_score

        metrics["val_accuracy"] = float(accuracy_score(y_val, preds))
        metrics["val_f1"] = float(f1_score(y_val, preds, average="weighted"))
        if n_classes == 2:
            from sklearn.metrics import roc_auc_score

            try:
                proba = model.predict_proba(X_val)[:, 1]
                metrics["val_auc"] = float(roc_auc_score(y_val, proba))
            except Exception:
                pass
    else:
        from sklearn.metrics import mean_absolute_error, r2_score

        metrics["val_mae"] = float(mean_absolute_error(y_val, preds))
        metrics["val_r2"] = float(r2_score(y_val, preds))

    if test_path:
        test = pd.read_parquet(test_path)
        X_test, y_test = test[feature_cols], test[target]
        test_preds = model.predict(X_test)
        if is_clf:
            from sklearn.metrics import accuracy_score

            metrics["test_accuracy"] = float(accuracy_score(y_test, test_preds))
        else:
            from sklearn.metrics import mean_absolute_error, r2_score

            metrics["test_mae"] = float(mean_absolute_error(y_test, test_preds))
            metrics["test_r2"] = float(r2_score(y_test, test_preds))

    # Persist
    save = Path(save_to)
    save.parent.mkdir(parents=True, exist_ok=True)
    with open(save, "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols, "target": target}, f)

    metrics["model_path"] = str(save)
    metrics["feature_importance_top10"] = sorted(
        zip(feature_cols, model.feature_importances_.tolist()),
        key=lambda kv: kv[1],
        reverse=True,
    )[:10]
    return metrics
