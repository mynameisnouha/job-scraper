"""The extraction target: what one job posting looks like once it is normalised.

Everything here is a *closed vocabulary*. That is the whole point of this file.

A free-text extraction ("what skills does this job want?") produces a different
spelling every time - LLMs, large language models, Sprachmodelle, GenAI - and
the feature space fragments into hundreds of near-duplicate columns that no
clustering can recover from. Forcing the model to choose from a fixed list makes
two postings that mean the same thing land on the same axis, which is the only
reason distances between jobs mean anything downstream.

The lists are deliberately short. Every value has to be a distinction that could
plausibly change what goes on a CV; anything finer is noise at this corpus size.
"""

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


# Boundary rules for the skill vocabulary, shared verbatim by the job extractor
# and the CV extractor.
#
# They have to be shared. The two sides are compared directly - a CV is scored
# against an archetype's demand - so any rule applied to one and not the other
# shows up as a fake gap. That is not hypothetical: without these notes the model
# read `deep_learning` as mutually exclusive with `computer_vision` and `nlp`, so
# postings wanting a neural network were tagged deep_learning while a CV that
# fine-tuned a transformer was not, and the mismatch surfaced as the single
# largest "missing skill" in the report.
TAXONOMY_NOTES = """Boundary rules for the skill vocabulary:

- The categories are NOT mutually exclusive. Assign every one that applies.
- deep_learning covers any neural network: CNNs, transformers, fine-tuned LLMs. \
A vision CNN is BOTH deep_learning and computer_vision. A fine-tuned language \
model is BOTH deep_learning and nlp.
- classical_ml means NON-neural methods only: trees, boosting, SVM, k-means, \
regression. A neural network is never classical_ml.
- llm_apis means calling a hosted model. finetuning means training or adapting \
model weights. They are independent - many roles want one and not the other.
- nlp covers language work generally; llm_apis, rag and agents are the specific \
LLM-era techniques. Tag the specific ones when they are named rather than \
falling back to nlp.
- cloud covers AWS, Azure or GCP generally. docker, kubernetes and iac_terraform \
are separate and should only be tagged when actually named.
- backend_api means building services or APIs, including FastAPI and REST work."""


class Skill(str, Enum):
    """Concrete, checkable capabilities a JD can ask for.

    Grouped by block in the source order below, which is also the order they
    appear in the feature matrix - handy when reading a centroid by eye.
    """

    # languages & general engineering
    PYTHON = "python"
    SQL = "sql"
    JVM_OR_CPP = "jvm_or_cpp"
    JS_TS = "js_ts"
    GIT_TESTING = "git_testing"

    # modelling
    DEEP_LEARNING = "deep_learning"
    CLASSICAL_ML = "classical_ml"
    STATISTICS_EXPERIMENTS = "statistics_experiments"
    TIME_SERIES = "time_series"
    COMPUTER_VISION = "computer_vision"
    NLP = "nlp"
    OPTIMIZATION_OR = "optimization_or"

    # the LLM stack, split finely because this is where the market is moving
    LLM_APIS = "llm_apis"
    RAG = "rag"
    AGENTS = "agents"
    FINETUNING = "finetuning"
    INFERENCE_OPTIMIZATION = "inference_optimization"
    PROMPT_ENGINEERING = "prompt_engineering"
    LLM_EVALUATION = "llm_evaluation"
    VECTOR_DATABASES = "vector_databases"

    # data engineering
    ETL_PIPELINES = "etl_pipelines"
    SPARK_DATABRICKS = "spark_databricks"
    ORCHESTRATION = "orchestration"
    STREAMING = "streaming"
    DATA_WAREHOUSE = "data_warehouse"
    DATA_MODELLING = "data_modelling"

    # platform & delivery
    CLOUD = "cloud"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    IAC_TERRAFORM = "iac_terraform"
    CICD = "cicd"
    MODEL_SERVING = "model_serving"
    MONITORING = "monitoring"

    # product surface
    BACKEND_API = "backend_api"
    FRONTEND = "frontend"

    # analytics surface
    BI_DASHBOARDS = "bi_dashboards"
    SPREADSHEET_REPORTING = "spreadsheet_reporting"


class PrimaryFunction(str, Enum):
    """What the person hired into this role spends most of their week doing.

    This is the most CV-relevant field in the schema. Two jobs can want an
    identical stack and still need opposite CVs, because one ships a product and
    the other answers questions.
    """

    BUILD_AI_PRODUCT = "build_ai_product"          # LLM/ML features shipped to users
    ML_ENGINEERING = "ml_engineering"              # train, deploy, maintain models
    BUILD_DATA_PLATFORM = "build_data_platform"    # pipelines, warehouse, infra
    ANALYTICS_INSIGHT = "analytics_insight"        # analyse, report, dashboards
    RESEARCH_SCIENCE = "research_science"          # novel methods, papers, prototypes
    CONSULTING_DELIVERY = "consulting_delivery"    # client-facing project work
    SOFTWARE_ENGINEERING = "software_engineering"  # general application/backend
    OPS_AUTOMATION = "ops_automation"              # internal process automation


class Deliverable(str, Enum):
    """The artefact the role is judged on - what "done" looks like."""

    PRODUCTION_SYSTEM = "production_system"
    RECURRING_ANALYSIS = "recurring_analysis"
    PROTOTYPE_POC = "prototype_poc"
    RESEARCH_OUTPUT = "research_output"
    CLIENT_DELIVERABLE = "client_deliverable"
    INTERNAL_TOOLING = "internal_tooling"


class TeamContext(str, Enum):
    """Who you sit with. Decides whether a CV leads with impact or with method."""

    EMBEDDED_PRODUCT_TEAM = "embedded_product_team"
    CENTRAL_DATA_TEAM = "central_data_team"
    RND_LAB = "rnd_lab"
    CONSULTANCY = "consultancy"
    SOLO_OR_FIRST_HIRE = "solo_or_first_hire"
    ACADEMIC_GROUP = "academic_group"
    IT_DEPARTMENT = "it_department"


class CompanyStage(str, Enum):
    STARTUP_LT50 = "startup_lt50"
    SCALEUP = "scaleup"
    MITTELSTAND = "mittelstand"
    LARGE_ENTERPRISE = "large_enterprise"
    PUBLIC_OR_RESEARCH = "public_or_research"
    AGENCY_OR_STAFFING = "agency_or_staffing"


class Seniority(str, Enum):
    """The level the JD is really pitched at, not the word in the title."""

    ENTRY = "entry"
    JUNIOR_1_2 = "junior_1_2"
    MID_3_5 = "mid_3_5"
    SENIOR_5_PLUS = "senior_5_plus"
    LEAD = "lead"


class Domain(str, Enum):
    AUTOMOTIVE_MANUFACTURING = "automotive_manufacturing"
    FINANCE_INSURANCE = "finance_insurance"
    HEALTHCARE_PHARMA = "healthcare_pharma"
    RETAIL_ECOMMERCE = "retail_ecommerce"
    ENERGY_UTILITIES = "energy_utilities"
    PUBLIC_SECTOR = "public_sector"
    TECH_SAAS = "tech_saas"
    TELECOM_MEDIA = "telecom_media"
    LOGISTICS_MOBILITY = "logistics_mobility"
    RESEARCH_ACADEMIA = "research_academia"
    CROSS_INDUSTRY = "cross_industry"


class JobProfile(BaseModel):
    """One posting, normalised. The unit the clustering actually sees."""

    must_have_skills: List[Skill] = Field(
        default_factory=list,
        description=(
            "Skills the JD presents as REQUIRED. Only what is actually named or "
            "unmistakably implied - do not pad with what a role like this usually "
            "wants. Max 12."
        ),
    )
    nice_to_have_skills: List[Skill] = Field(
        default_factory=list,
        description="Skills named as a plus / von Vorteil / wuenschenswert. Max 8.",
    )
    primary_function: PrimaryFunction = Field(
        ..., description="What this person does most of the week. Pick exactly one."
    )
    secondary_function: Optional[PrimaryFunction] = Field(
        None,
        description=(
            "A clear second function if the role is genuinely split. Null when the "
            "role has one job - most do. Do not fill this speculatively."
        ),
    )
    deliverable: Deliverable = Field(..., description="What being done looks like here.")
    team_context: TeamContext = Field(..., description="Who this person sits with.")
    company_stage: CompanyStage = Field(..., description="Employer type and size.")
    seniority: Seniority = Field(
        ...,
        description=(
            "The level the RESPONSIBILITIES imply, not the adjective in the title. "
            "A Senior title asking for 2 years is mid_3_5 at most."
        ),
    )
    domain: Domain = Field(
        ..., description="Industry. Use cross_industry when the JD is domain-agnostic."
    )

    years_required: int = Field(
        0, ge=0, le=20,
        description="Minimum years explicitly required. 0 if unstated or entry-level.",
    )
    german_required: str = Field(
        "unstated",
        description=(
            "German level DEMANDED, never the language the ad is written in: "
            "none, nice-to-have, B2, C1-fluent, or unstated."
        ),
    )

    role_summary: str = Field(
        ...,
        description=(
            "One sentence in ENGLISH describing the role, whatever language the ad "
            "is in - for a human reading cluster exemplars. Max 25 words."
        ),
    )
    top_requirements: List[str] = Field(
        default_factory=list,
        description="The 3 requirements the JD leads with, in English, max 10 words each.",
    )

    # --- Tolerance for the model inventing vocabulary -------------------------
    #
    # A closed schema does not stop the model reaching for a word that isn't in
    # it: "legal_tech" for domain, "video understanding" and "tracking" for
    # skills. The question is what to do about it, and the answer differs by
    # field, so the two cases are handled separately rather than with one blanket
    # rule.

    @field_validator("must_have_skills", "nice_to_have_skills", mode="before")
    @classmethod
    def _drop_unknown_skills(cls, value: Any) -> Any:
        """Discard skills outside the vocabulary instead of failing the profile.

        An invented entry in a 12-item skill list is a rounding error; throwing
        away the whole posting over it loses the eleven good entries, the
        function, the deliverable and the context along with it. The skill
        blocks are also the ones the IDF weighting is most forgiving of.
        """
        if not isinstance(value, list):
            return value
        known = {s.value for s in Skill}
        return [v for v in value if not isinstance(v, str) or v in known]

    @field_validator("domain", mode="before")
    @classmethod
    def _unknown_domain_is_cross_industry(cls, value: Any) -> Any:
        """Fall back to cross_industry, which is genuinely the none-of-these bucket.

        Deliberately NOT done for primary_function, deliverable, team_context,
        company_stage or seniority: those have no neutral value, so any fallback
        would be a guess that pulls a centroid the wrong way. Better to drop the
        posting and see it in the failure count than to cluster on a fabrication.
        """
        if isinstance(value, str) and value not in {d.value for d in Domain}:
            return Domain.CROSS_INDUSTRY.value
        return value
