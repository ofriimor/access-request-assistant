id: POL-CRM-01
title: CRM Access Policy
system: crm
allowed_departments: Sales, Support, Marketing
max_access_level: write
default_duration_days: 90
max_duration_days: 180
---
# CRM Access Policy (Salesforce CRM)

The CRM holds customer accounts, contacts, deals and pipeline data.

- Read access (view accounts, deals, reports): Sales, Support and Marketing employees.
- Write access (edit accounts, update deals, change pipeline): Sales and Support employees
  with a business need such as taking over accounts or handling escalations.
- Marketing receives read access for reporting and campaign analysis.
- Maximum duration is 180 days; default is 90 days.
- Admin access to the CRM is managed by the CRM platform team only.
