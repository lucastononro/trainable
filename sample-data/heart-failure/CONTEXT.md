# Heart Failure Prediction

**Source:** [Kaggle - fedesoriano/heart-failure-prediction](https://www.kaggle.com/datasets/fedesoriano/heart-failure-prediction)
**License:** Open Database License (ODbL 1.0)
**Task:** Binary Classification

## Overview

918 observations with 11 clinical features for predicting heart disease. Cardiovascular diseases are the #1 cause of death globally — this dataset supports early detection via ML.

Combined from 5 independent heart disease datasets (Cleveland, Hungarian, Switzerland, Long Beach VA, Stalog) originally from the UCI ML Repository, with 272 duplicates removed.

## Schema

| Column | Type | Description |
|--------|------|-------------|
| Age | int | Patient age in years |
| Sex | cat | M / F |
| ChestPainType | cat | TA, ATA, NAP, ASY |
| RestingBP | int | Resting blood pressure (mm Hg) |
| Cholesterol | int | Serum cholesterol (mm/dl) |
| FastingBS | bin | 1 if fasting blood sugar > 120 mg/dl |
| RestingECG | cat | Normal, ST, LVH |
| MaxHR | int | Maximum heart rate achieved (60-202) |
| ExerciseAngina | cat | Y / N |
| Oldpeak | float | ST depression (numeric) |
| ST_Slope | cat | Up, Flat, Down |
| **HeartDisease** | **target** | **1 = heart disease, 0 = normal** |

## Why this dataset

- Small and fast (918 rows, 12 cols) — perfect for quick end-to-end demos
- Good mix of numeric + categorical features
- Slight class imbalance exercises SMOTE/balancing strategies
- Clinically meaningful features produce interpretable SHAP plots
