id: POL-PROD-03
title: Production Database Access Policy
system: prod_db
allowed_departments: Engineering
max_access_level: read
default_duration_days: 14
max_duration_days: 30
---
# Production Database Access Policy

The production database holds live customer data.

- Only Engineering employees may receive access, and only read-only access
  (for debugging and investigation).
- Write or admin access to production is never granted through self-service.
  Data fixes must go through a reviewed migration or the on-call change process.
- Maximum duration is 30 days; default is 14 days.
