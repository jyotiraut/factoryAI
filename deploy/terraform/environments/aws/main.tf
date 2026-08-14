# Composition root for a single environment (default: staging). Wires the four
# cloud-agnostic modules Phase 14's ROADMAP scope names — object storage, managed
# Postgres, container registry, secrets — with AWS as the reference implementation.
# Networking (VPC/subnets) is assumed to already exist; see variables.tf's vpc_id/
# subnet_ids for why that boundary was drawn here.

module "object_storage" {
  source = "../../modules/object-storage"

  for_each = var.object_storage_buckets

  bucket_name = each.value
  environment = var.environment
  tags        = var.tags
}

module "managed_postgres" {
  source = "../../modules/managed-postgres"

  identifier                 = "factoryai-${var.environment}"
  environment                = var.environment
  instance_class             = var.db_instance_class
  multi_az                   = var.db_multi_az
  master_password            = var.db_master_password
  vpc_id                     = var.vpc_id
  subnet_ids                 = var.subnet_ids
  allowed_security_group_ids = var.db_allowed_security_group_ids
  allowed_cidr_blocks        = var.db_allowed_cidr_blocks
  tags                       = var.tags
}

module "container_registry" {
  source = "../../modules/container-registry"

  repository_name = var.ecr_repository_name
  environment     = var.environment
  tags            = var.tags
}

module "secrets" {
  source = "../../modules/secrets"

  secret_name = var.secret_name
  environment = var.environment
  tags        = var.tags

  # Keys match the exact env var names shared/config.py's Settings tree reads, so a
  # Kubernetes-side secret sync (External Secrets Operator or similar — Helm's concern,
  # not this module's) can project each key straight into the application container.
  secret_values = {
    POSTGRES_PASSWORD  = var.db_master_password
    JWT_SECRET_KEY     = var.jwt_secret_key
    STORAGE_ACCESS_KEY = var.storage_access_key
    STORAGE_SECRET_KEY = var.storage_secret_key
  }
}
