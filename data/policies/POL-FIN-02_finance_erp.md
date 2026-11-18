id: POL-FIN-02
title: Finance ERP Access Policy
system: finance_erp
allowed_departments: Finance, Management
max_access_level: write
default_duration_days: 30
max_duration_days: 90
---
# Finance ERP Access Policy (SAP Finance)

The finance ERP holds invoices, payments, ledger and payroll cost data.

- Access is limited to the Finance department and Management.
- Employees outside Finance must request a report export from Finance instead of
  direct ERP access.
- Write access (post invoices, approve payments) requires segregation-of-duties review.
- Maximum duration is 90 days; default is 30 days.
