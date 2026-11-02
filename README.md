# Database_Project

# Index-Request Fidelity: InnoDB vs MEMORY

COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor

Measuring how MySQL InnoDB substitutes HASH index requests with B-trees,
and how its Adaptive Hash Index behaves under shifting read/write workloads.

## Team
- Khushi Ranpariya
- Venu Gopal Reddy Jakku
- Malik Oyewole

## Links
- Project management board: [add link]
- Documentation: see /docs

## Repository Structure
- `docs/`: Requirements (D.4.1), Design (D.4.2), and Midterm Status (D.3.1)
- `src/`: Python orchestrator, fidelity auditor, and telemetry modules
- `configs/`: YAML configurations for the six experimental cells (A to F)
- `sql/`: MySQL schema definitions for InnoDB and MEMORY engines
- `lua/`: Custom Sysbench Lua scripts for shifting read/write workloads
- `docker/`: Docker Compose files for reproducible cell provisioning
- `analysis/`: Statistical aggregation and analysis notebooks
- `results/`: Raw telemetry and aggregated CSV/JSON results

## Status
**Midterm Implementation Phase (D.3.1)**
The requirements and system architecture design are complete. We are currently implementing the Python-based orchestrator, Docker containerization for the MySQL 8.4 LTS cells, and the sysbench workload driver.
