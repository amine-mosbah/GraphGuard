"""Platform layer — the deployable service around the analytics core.

Everything in this package exists because GraphGuard is deployed as a multi-user service rather
than run as a script: deployment settings, database access, authentication, background jobs.

The boundary is deliberate and worth stating, because it is what keeps the thesis contribution
separable from the plumbing around it. Nothing in :mod:`graphguard.schema`,
:mod:`graphguard.graph`, :mod:`graphguard.detection`, :mod:`graphguard.correlation` or
:mod:`graphguard.eval` imports from here. Those modules run identically from the CLI with no
database, no Redis and no user accounts, which is what makes the evaluation harness reproducible
from a single seed. Dependencies point one way only: platform depends on analytics, never the
reverse.
"""
