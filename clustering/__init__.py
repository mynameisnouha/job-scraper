"""Grouping the jobs you can actually get into a handful of CV archetypes.

The pipeline is four steps, each its own module:

    select   ->  extract  ->  features  ->  cluster
    the jobs     one cheap    a weighted    k-means +
    worth a      LLM call     block         stability
    CV           per job      vector        check

Run it with ``python -m clustering.run``. See README.md for why it is built
this way rather than embedding the job descriptions directly.
"""
