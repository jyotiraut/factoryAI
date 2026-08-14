variable "identifier" {
  description = "RDS instance identifier, e.g. factoryai-staging."
  type        = string
}

variable "environment" {
  description = "Deployment environment, applied as a tag."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL engine version. Pinned rather than left to float, so a `terraform apply` never silently upgrades a production database."
  type        = string
  default     = "16.4"
}

# db.t3.micro-class: a small burstable instance, matching this platform's development-scale
# reference deployment (a single MVTec category, Phase 2's docker-compose Postgres). Bump
# to an m-class/r-class instance for a real production workload with more than one category.
variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "allocated_storage_gb" {
  description = "Initial allocated storage, in GiB."
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "Ceiling for RDS storage autoscaling. Set equal to allocated_storage_gb to disable autoscaling."
  type        = number
  default     = 100
}

variable "multi_az" {
  description = "Run a synchronous standby in a second AZ. Off by default (single-AZ, matching the docker-compose reference's single Postgres container); turn on for a real production environment that needs automatic failover."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backup retention window, in days."
  type        = number
  default     = 7
}

variable "backup_window" {
  description = "Daily UTC window RDS takes automated backups in, HH:MM-HH:MM."
  type        = string
  default     = "03:00-04:00"
}

variable "maintenance_window" {
  description = "Weekly UTC window for RDS-applied maintenance, ddd:HH:MM-ddd:HH:MM."
  type        = string
  default     = "sun:04:30-sun:05:30"
}

variable "db_name" {
  description = "Database name created on the instance. Matches shared/config.py's DatabaseSettings.db default (POSTGRES_DB)."
  type        = string
  default     = "factoryai"
}

variable "master_username" {
  description = "Master username. Matches DatabaseSettings.user (POSTGRES_USER)."
  type        = string
  default     = "factoryai"
}

variable "master_password" {
  description = "Master password. Matches DatabaseSettings.password (POSTGRES_PASSWORD) — never given a default; must come from a tfvars file that is never committed, or better, from the secrets module's Secrets Manager entry."
  type        = string
  sensitive   = true
}

variable "vpc_id" {
  description = "VPC the instance and its security group are created in."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the DB subnet group. RDS requires at least two, in at least two AZs, even for a single-AZ instance."
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach Postgres on port 5432. Prefer allowed_security_group_ids for anything running in the same VPC (the application's own ECS/EKS pods); use this only for CIDR-addressed access such as a bastion or a CI runner."
  type        = list(string)
  default     = []
}

variable "allowed_security_group_ids" {
  description = "Security group IDs allowed to reach Postgres on port 5432 — the application's task/pod security group, so ingress is scoped to the app rather than a CIDR range."
  type        = list(string)
  default     = []
}

variable "port" {
  description = "PostgreSQL port."
  type        = number
  default     = 5432
}

variable "tags" {
  description = "Tags applied to every resource this module creates, merged with the module's own environment tag."
  type        = map(string)
  default     = {}
}
