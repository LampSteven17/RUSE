"""Derive descriptive metadata (dataset / gpu_tier) from a deployment_name.

Used by register_experiment.py at deploy time to populate experiments.json
entries with structured identity fields. Strictly descriptive — no logic
hangs off these. PHASE can filter "all sum25 deploys" or "all rtx-a deploys"
without substring-parsing deployment_name strings.

Each deploy is its own independent entry. Tier variants of the same dataset
(e.g. sum25 on V100 and sum25 on RTX-A) are unrelated to RUSE — they share
no automatic linkage, no supersession, no cross-stamping. Operator manages
teardown explicitly.
"""

# Known gpu_tier suffixes, longest-first so '-rtx-a' matches before '-rtx'.
_GPU_TIER_SUFFIXES = ("-rtx-a", "-rtx")


def derive_metadata(deployment_name: str, gpu_tier_hint: str | None = None) -> dict:
    """Parse a deployment_name into {dataset, gpu_tier}.

    gpu_tier_hint comes from config.yaml's `gpu_tier:` field and overrides
    suffix detection — useful when a new tier is added before this module
    learns its suffix.

    Returns all-null fields when the name doesn't match a recognized shape.
    """
    null_meta = {"dataset": None, "gpu_tier": gpu_tier_hint}
    parts = deployment_name.split("-", 2)
    if len(parts) < 2:
        return null_meta
    type_prefix, kind = parts[0], parts[1]
    if type_prefix not in ("decoy", "rampart", "ghosts"):
        return null_meta

    if kind == "controls":
        return {"dataset": None, "gpu_tier": gpu_tier_hint}
    if kind != "feedback":
        return null_meta

    # feedback: gpu_tier precedence is hint > known suffix > deploy-type default
    base = deployment_name
    gpu_tier = gpu_tier_hint
    if gpu_tier is None:
        for suffix in _GPU_TIER_SUFFIXES:
            if deployment_name.endswith(suffix):
                gpu_tier = suffix.lstrip("-")
                base = deployment_name[: -len(suffix)]
                break
        else:
            gpu_tier = "v100" if type_prefix == "decoy" else None
    else:
        suffix = f"-{gpu_tier}"
        if gpu_tier != "v100" and deployment_name.endswith(suffix):
            base = deployment_name[: -len(suffix)]

    feedback_prefix = f"{type_prefix}-feedback-"
    if not base.startswith(feedback_prefix):
        return {"dataset": None, "gpu_tier": gpu_tier}
    inner = base[len(feedback_prefix):]
    tokens = inner.split("-")
    if len(tokens) < 3:
        return {"dataset": None, "gpu_tier": gpu_tier}
    # tokens = [preset, dataset_chunks..., scope]. We drop preset (always
    # "stdctrls" in practice) and scope (always "all"), keep middle as dataset.
    return {
        "dataset": "-".join(tokens[1:-1]),
        "gpu_tier": gpu_tier,
    }
