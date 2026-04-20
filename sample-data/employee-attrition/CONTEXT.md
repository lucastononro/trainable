# Employee Attrition (IBM HR Analytics)

**Source:** [Kaggle - patelprashant/employee-attrition](https://www.kaggle.com/datasets/patelprashant/employee-attrition)
**Origin:** IBM Watson Analytics — fictional dataset
**License:** Data files (c) Original Authors
**Task:** Binary Classification

## Overview

1,470 employee records with 35 features covering demographics, job role, satisfaction scores, compensation, and work history. The goal is to predict whether an employee will leave (attrition). Created by IBM data scientists as a realistic fictional HR dataset.

## Schema (key columns)

| Column | Type | Description |
|--------|------|-------------|
| Age | int | Employee age |
| **Attrition** | **target** | **Yes / No** |
| BusinessTravel | cat | Non-Travel / Travel_Rarely / Travel_Frequently |
| DailyRate | int | Daily rate of pay |
| Department | cat | Sales / R&D / HR |
| DistanceFromHome | int | Distance from home (miles) |
| Education | ord | 1=Below College, 2=College, 3=Bachelor, 4=Master, 5=Doctor |
| EnvironmentSatisfaction | ord | 1-4 scale |
| Gender | cat | Male / Female |
| JobInvolvement | ord | 1-4 scale |
| JobLevel | int | 1-5 |
| JobRole | cat | 9 roles (Sales Executive, Research Scientist, etc.) |
| JobSatisfaction | ord | 1-4 scale |
| MaritalStatus | cat | Single / Married / Divorced |
| MonthlyIncome | int | Monthly income |
| OverTime | cat | Yes / No |
| PerformanceRating | ord | 1-4 scale |
| WorkLifeBalance | ord | 1-4 scale |
| YearsAtCompany | int | Tenure at current company |
| ... | ... | *35 columns total — see CSV header for full list* |

## Why this dataset

- Rich feature set (35 cols) with ordinal, nominal, and numeric types
- Class imbalance (~16% attrition) exercises SMOTE and sampling strategies
- Multiple satisfaction/rating ordinals test encoding choices
- Constant columns (EmployeeCount=1, StandardHours=80) test feature selection
- HR use case is universally relatable and business-meaningful
- 1,470 rows trains fast while having enough signal for good models
