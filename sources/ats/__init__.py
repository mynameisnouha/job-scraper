"""Career-page feeds, one adapter per applicant tracking system."""
from sources.ats import ashby, greenhouse, lever, personio, recruitee, smartrecruiters, workable

ADAPTERS = {
    adapter.ats_type: adapter
    for adapter in (greenhouse.ADAPTER, lever.ADAPTER, ashby.ADAPTER,
                    smartrecruiters.ADAPTER, recruitee.ADAPTER, workable.ADAPTER, personio.ADAPTER)
}
