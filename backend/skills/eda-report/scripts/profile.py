"""One-shot dataset profiler. Drop-in for the eda-report skill.

Usage from inside an agent's sandbox:
    import sys; sys.path.insert(0, '/skills/eda-report/scripts')
    import profile
    profile.run('/data/projects/<pid>/datasets/<file>.csv',
                target='label',
                out_dir='/data/sessions/<sid>/figures/')
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _read(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    if path.endswith((".csv", ".tsv", ".txt")):
        sep = "\t" if path.endswith(".tsv") else ","
        return pd.read_csv(path, sep=sep)
    if path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    raise ValueError(f"Unsupported file type: {path}")


def _outliers_iqr(s: pd.Series) -> int:
    s = s.dropna()
    if s.empty:
        return 0
    q1, q3 = np.percentile(s, [25, 75])
    iqr = q3 - q1
    return int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())


def run(path: str, target: str | None = None, out_dir: str = "./figures") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    df = _read(path)
    profile: dict = {
        "shape": list(df.shape),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1e6, 3),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "missing": {
            c: {
                "count": int(df[c].isna().sum()),
                "pct": round(float(df[c].isna().mean()) * 100, 2),
            }
            for c in df.columns
        },
        "duplicate_rows": int(df.duplicated().sum()),
        "numeric": {},
        "categorical": {},
        "target": target,
    }

    num_cols = df.select_dtypes(include=np.number).columns.tolist()
    cat_cols = [c for c in df.columns if c not in num_cols]

    for c in num_cols:
        s = df[c]
        profile["numeric"][c] = {
            "mean": float(s.mean()) if s.notna().any() else None,
            "median": float(s.median()) if s.notna().any() else None,
            "std": float(s.std()) if s.notna().any() else None,
            "min": float(s.min()) if s.notna().any() else None,
            "max": float(s.max()) if s.notna().any() else None,
            "skew": float(s.skew()) if s.notna().any() else None,
            "outliers_iqr": _outliers_iqr(s),
        }
        try:
            fig, ax = plt.subplots(figsize=(5, 3))
            s.dropna().hist(bins=30, ax=ax)
            ax.set_title(f"{c} — distribution")
            fig.tight_layout()
            fig.savefig(out / f"hist_{c}.png", dpi=120)
            plt.close(fig)
        except Exception:
            pass

    for c in cat_cols:
        s = df[c].astype("string")
        vc = s.value_counts(dropna=False).head(20)
        profile["categorical"][c] = {
            "cardinality": int(s.nunique(dropna=True)),
            "top": {str(k): int(v) for k, v in vc.items()},
            "rare_pct": round(
                float(
                    (s.value_counts(normalize=True) < 0.01).sum() / max(s.nunique(), 1)
                )
                * 100,
                2,
            ),
        }

    # Correlation heatmap (numeric only)
    if len(num_cols) >= 2:
        try:
            corr = df[num_cols].corr()
            fig, ax = plt.subplots(
                figsize=(
                    min(0.4 * len(num_cols) + 2, 12),
                    min(0.4 * len(num_cols) + 2, 12),
                )
            )
            im = ax.imshow(corr.values, vmin=-1, vmax=1, cmap="coolwarm")
            ax.set_xticks(range(len(num_cols)))
            ax.set_yticks(range(len(num_cols)))
            ax.set_xticklabels(num_cols, rotation=90, fontsize=8)
            ax.set_yticklabels(num_cols, fontsize=8)
            fig.colorbar(im, ax=ax, fraction=0.04)
            ax.set_title("Correlation matrix")
            fig.tight_layout()
            fig.savefig(out / "correlation_matrix.png", dpi=120)
            plt.close(fig)
            profile["high_correlation_pairs"] = [
                {"a": a, "b": b, "r": round(float(corr.loc[a, b]), 3)}
                for a in num_cols
                for b in num_cols
                if a < b and abs(corr.loc[a, b]) > 0.9
            ]
        except Exception:
            profile["high_correlation_pairs"] = []

    # Target signal (lightweight)
    if target and target in df.columns:
        y = df[target]
        is_classification = y.dtype == "object" or y.nunique() <= 20
        profile["target_info"] = {
            "type": "classification" if is_classification else "regression",
            "n_unique": int(y.nunique()),
        }
        if is_classification:
            try:
                fig, ax = plt.subplots(figsize=(5, 3))
                y.value_counts().plot(kind="bar", ax=ax)
                ax.set_title(f"{target} — class balance")
                fig.tight_layout()
                fig.savefig(out / f"target_{target}_balance.png", dpi=120)
                plt.close(fig)
            except Exception:
                pass

    profile_path = out / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2, default=str))
    return profile


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: profile.py <data_path> [target] [out_dir]")
        sys.exit(2)
    path = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()
    res = run(path, target=target, out_dir=out_dir)
    print(json.dumps({"shape": res["shape"], "out_dir": out_dir}, indent=2))
