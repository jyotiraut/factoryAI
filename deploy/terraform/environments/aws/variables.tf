variable "aws_region" {
  description = "AWS region every resource in this environment is created in."
  type        = string
  default     = "eu-central-1"
}

variable "environment" {
  description = "Environment name, e.g. staging. Threaded through as a tag and, where AWS naming allows, as part of resource names."
  type        = string
  default     = "staging"
}

variable "vpc_id" {
  description = "VPC the RDS instance and its security group are created in. This environment does not provision its own VPC — networking is expected to already exist (the cluster VPC Phase 14's Kubernetes/Helm scope targets), consistent with this module covering only the cloud-agnostic pieces the ROADMAP scopes it to (object storage, managed Postgres, container registry, secrets), not the network itself."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the RDS DB subnet group. At least two, in at least two AZs."
  type        = list(string)
}

variable "db_allowed_security_group_ids" {
  description = "Security group IDs allowed to reach Postgres — typically the EKS node/pod security group the application runs under."
  type        = list(string)
  default     = []
}

variable "db_allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach Postgres, in addition to any security groups above. Leave empty when the application always reaches the database from inside the same VPC."
  type        = list(string)
  default     = []
}

variable "db_master_password" {
  description = "RDS master password (POSTGRES_PASSWORD). Supply via a git-ignored terraform.tfvars or TF_VAR_db_master_password — never a committed default."
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "db_multi_az" {
  description = "Run RDS across two AZs with a synchronous standby."
  type        = bool
  default     = false
}

variable "object_storage_buckets" {
  description = "Bucket names this environment provisions, matching StorageSettings.buckets (shared/config.py): raw, datasets, artifacts, heatmaps."
  type        = map(string)
  default = {
    raw       = "factoryai-staging-raw"
    datasets  = "factoryai-staging-datasets"
    artifacts = "factoryai-staging-artifacts"
    heatmaps  = "factoryai-staging-heatmaps"
  }
}

variable "ecr_repository_name" {
  description = "ECR repository name for the application image (deploy/docker/factoryai.Dockerfile)."
  type        = string
  default     = "factoryai/api"
}

variable "secret_name" {
  description = "Secrets Manager secret name for this environment's application credentials."
  type        = string
  default     = "factoryai/staging/app"
}

variable "jwt_secret_key" {
  description = "JWT signing secret (JWT_SECRET_KEY / AuthSettings.secret_key). Supply via terraform.tfvars or TF_VAR_jwt_secret_key — never a committed default."
  type        = string
  sensitive   = true
}

variable "storage_access_key" {
  description = "S3 access key for the application's ObjectStore adapter (STORAGE_ACCESS_KEY / StorageSettings.access_key). For a real deployment this is typically an IAM user's or role's access key, provisioned outside this module and passed in here."
  type        = string
  sensitive   = true
}

variable "storage_secret_key" {
  description = "S3 secret key for the application's ObjectStore adapter (STORAGE_SECRET_KEY / StorageSettings.secret_key)."
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags applied to every resource in this environment."
  type        = map(string)
  default = {
    Project = "factoryai"
  }
}
