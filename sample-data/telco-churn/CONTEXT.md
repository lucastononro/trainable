# Telco Customer Churn

**Source:** [Kaggle - blastchar/telco-customer-churn](https://www.kaggle.com/datasets/blastchar/telco-customer-churn)
**Origin:** IBM Sample Data Sets
**License:** Data files (c) Original Authors
**Task:** Binary Classification

## Overview

7,043 customers from a telecom company in California, with data about services signed up for, account information, and demographics. The goal is to predict which customers will churn (leave the company).

## Schema

| Column | Type | Description |
|--------|------|-------------|
| customerID | str | Unique customer identifier |
| gender | cat | Male / Female |
| SeniorCitizen | bin | 0 / 1 |
| Partner | cat | Yes / No |
| Dependents | cat | Yes / No |
| tenure | int | Months with company |
| PhoneService | cat | Yes / No |
| MultipleLines | cat | Yes / No / No phone service |
| InternetService | cat | DSL / Fiber optic / No |
| OnlineSecurity | cat | Yes / No / No internet service |
| OnlineBackup | cat | Yes / No / No internet service |
| DeviceProtection | cat | Yes / No / No internet service |
| TechSupport | cat | Yes / No / No internet service |
| StreamingTV | cat | Yes / No / No internet service |
| StreamingMovies | cat | Yes / No / No internet service |
| Contract | cat | Month-to-month / One year / Two year |
| PaperlessBilling | cat | Yes / No |
| PaymentMethod | cat | Electronic check / Mailed check / Bank transfer / Credit card |
| MonthlyCharges | float | Monthly charge amount |
| TotalCharges | str* | Total charges (*has blanks for new customers) |
| **Churn** | **target** | **Yes / No** |

## Why this dataset

- Best all-around demo: mixed types, missing values, class imbalance
- Business-intuitive target — everyone understands customer churn
- Multiple categorical encoding strategies needed (ordinal, one-hot, target)
- Good for showcasing SHAP feature importance in a business context
- 7k rows trains fast but is large enough for meaningful validation
