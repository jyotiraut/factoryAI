# Terraform — cloud-agnostic infrastructure (Phase 14)

Modules for the four cloud-agnostic pieces the ROADMAP's Phase 14 scope names — object
storage, managed Postgres, container registry, secrets — with AWS as the reference
implementation, plus a root module (`environments/aws/`) composing them into a single
"staging"-shaped environment.

```
deploy/terraform/
├── modules/
│   ├── object-storage/      # S3 bucket (versioning, lifecycle, SSE, block-public-access)
│   ├── managed-postgres/    # RDS PostgreSQL (subnet group, security group, backups)
│   ├── container-registry/  # ECR repository (scan-on-push, lifecycle policy)
│   └── secrets/             # Secrets Manager: one JSON secret per environment
└── environments/
    └── aws/                 # Root module composing all four for one environment
```

## Verification status — read this before trusting a "terraform validate passed" claim

**No real AWS account or credentials exist in this environment.** `terraform apply` was
never run against real infrastructure, and none of these modules have been exercised
end-to-end against AWS. Everything here was verified with:

- `terraform fmt -recursive` — formatting only.
- `terraform init -backend=false && terraform validate` — HCL syntax and internal
  consistency (variable references, resource argument names against the pinned provider
  schema), run both for the root `environments/aws` module and for each module directory
  standalone.

Neither check catches a bad AWS-side value (a region that doesn't exist, an engine version
AWS has deprecated, a quota that would be exceeded) or a wrong IAM permission — that only
surfaces on a real `plan`/`apply`. Anyone using this for real should run `terraform plan`
against a real account before ever running `apply`.

## Using this for real

1. Provide AWS credentials the normal way (`aws configure`, environment variables, or an
   assumed role) — nothing here is hardcoded to an account.
2. `cd environments/aws`, copy `terraform.tfvars.example` to `terraform.tfvars`, and fill in
   every `REPLACE_ME` placeholder: your VPC/subnet IDs, the application's security group,
   and generated secrets (`db_master_password`, `jwt_secret_key`, `storage_access_key`,
   `storage_secret_key`). `terraform.tfvars` is git-ignored — it is never meant to be
   committed.
3. Add a real state backend (an `backend "s3" { ... }` block in `versions.tf`) before the
   first apply — none is configured here, since there is no bucket yet to point one at.
4. `terraform init`, `terraform plan`, review, then `terraform apply`.

## Why these modules, this shape

- **`object-storage`** is one bucket per call, matching `StorageSettings.buckets`
  (`src/factoryai/shared/config.py`) — four independent bucket names
  (`factoryai-raw`/`-datasets`/`-artifacts`/`-heatmaps`), not one bucket with four
  prefixes. `environments/aws` calls it once per entry in `var.object_storage_buckets`.
- **`managed-postgres`** outputs `address`/`port`/`db_name` because that is exactly the
  shape `DatabaseSettings` (`POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`) already expects —
  swapping the local docker-compose Postgres for this instance is a host/port change, not a
  connection-string rewrite.
- **`secrets`** stores one JSON blob per environment keyed by the exact env var names
  `Settings` reads (`POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `STORAGE_ACCESS_KEY`,
  `STORAGE_SECRET_KEY`), so a Kubernetes-side secret sync can project each key straight into
  the application container's environment unchanged — that sync mechanism itself is Helm's
  concern (`deploy/helm/`), not this module's.
- **`container-registry`** is a single ECR repository for the application image built from
  `deploy/docker/factoryai.Dockerfile`; no equivalent exists in the local compose stack,
  since Phase 9 already noted there was no application image to containerise until Phase 14.

## Judgment calls worth flagging

- RDS `deletion_protection = true` and a mandatory final snapshot are hardcoded, not
  exposed as variables — the module treats "don't accidentally destroy the database" as
  non-negotiable rather than a per-environment toggle.
- `managed-postgres` defaults to `db.t3.micro`, single-AZ — sized for this platform's
  reference deployment (one MVTec category), not a multi-tenant production load; bump
  `db_instance_class`/`db_multi_az` for real production traffic.
- ECR's `image_tag_mutability = "IMMUTABLE"` by default: a CI-pushed tag (a Git SHA) should
  never be silently overwritten, the same principle ADR-0004/ADR-0006 already apply to model
  and dataset artifact identity.
- The root module assumes an existing VPC/subnets rather than provisioning its own —
  Phase 14's ROADMAP scope line names object storage, managed Postgres, container registry
  and secrets specifically; the network itself is the Kubernetes/Helm side of this phase.
