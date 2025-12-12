# OpenStack Flavors

Available flavors for SUP deployment.

## GPU Flavors

| Flavor Name | vCPUs | RAM | GPU | PCI Passthrough |
|-------------|-------|-----|-----|-----------------|
| rtx2080ti-1gpu.14vcpu.28g | 14 | 28GB | RTX 2080 Ti | `rtx2080ti:1` |
| rtx2080ti-A-1gpu.14vcpu.28g | 14 | 28GB | RTX 2080 Ti (A) | `2080ti-rtx-a:1` |
| v100-1gpu.14vcpu.28g | 14 | 28GB | V100 | `v100:1` |

## Non-GPU Flavors

| Flavor Name | vCPUs | RAM | GPU | Notes |
|-------------|-------|-----|-----|-------|
| v1.14vcpu.28g | 14 | 28GB | None | For M1 (MCHP) only |

## Usage Notes

- GPU flavors required for LLM-based behaviors (S*, B*, M2*, M3*)
- M1 (pure MCHP) can run on non-GPU flavors
- Capacity limits defined in playbook `flavor_capacity` vars
