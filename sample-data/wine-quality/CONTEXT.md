# Wine Quality

**Source:** [Kaggle - uciml/red-wine-quality](https://www.kaggle.com/datasets/uciml/red-wine-quality-cortez-et-al-2009)
**Origin:** UCI Machine Learning Repository
**License:** Open Database License (ODbL 1.0)
**Task:** Regression (or Multiclass Classification)

## Overview

Physicochemical properties and sensory quality scores for Portuguese "Vinho Verde" wines. Two files included:
- **winequality-red.csv** — 1,599 red wine samples
- **winequality-white.csv** — 4,898 white wine samples

Can be used as regression (predict quality score 0-10) or classification (good vs. not good, or multiclass buckets).

## Schema

| Column | Type | Description |
|--------|------|-------------|
| fixed acidity | float | Tartaric acid (g/dm3) |
| volatile acidity | float | Acetic acid (g/dm3) |
| citric acid | float | Citric acid (g/dm3) |
| residual sugar | float | Residual sugar (g/dm3) |
| chlorides | float | Sodium chloride (g/dm3) |
| free sulfur dioxide | float | Free SO2 (mg/dm3) |
| total sulfur dioxide | float | Total SO2 (mg/dm3) |
| density | float | Density (g/cm3) |
| pH | float | pH level |
| sulphates | float | Potassium sulphate (g/dm3) |
| alcohol | float | Alcohol (% vol) |
| **quality** | **target** | **Score between 3 and 9** |

## Why this dataset

- Dual personality: regression or classification — shows agent auto-detection
- All-numeric features, clean data, no missing values
- Compact at 11 features — trains very quickly
- Imbalanced classes (most wines score 5-6) exercises handling strategies
- Two files can be used separately or combined for a richer experiment

## Citation

P. Cortez, A. Cerdeira, F. Almeida, T. Matos and J. Reis. *Modeling wine preferences by data mining from physicochemical properties.* Decision Support Systems, Elsevier, 47(4):547-553, 2009.
