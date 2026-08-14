output "bucket_names" {
  description = "Provisioned bucket names, keyed the same as var.object_storage_buckets (raw/datasets/artifacts/heatmaps)."
  value       = { for key, mod in module.object_storage : key => mod.bucket_name }
}

output "bucket_arns" {
  description = "Provisioned bucket ARNs, keyed the same as var.object_storage_buckets."
  value       = { for key, mod in module.object_storage : key => mod.bucket_arn }
}

output "database_endpoint" {
  description = "RDS endpoint, host:port — split into POSTGRES_HOST/POSTGRES_PORT."
  value       = module.managed_postgres.endpoint
}

output "database_address" {
  description = "RDS host name only — feeds POSTGRES_HOST directly."
  value       = module.managed_postgres.address
}

output "database_port" {
  description = "RDS port — feeds POSTGRES_PORT."
  value       = module.managed_postgres.port
}

output "database_name" {
  description = "Database name — feeds POSTGRES_DB."
  value       = module.managed_postgres.db_name
}

output "ecr_repository_url" {
  description = "ECR repository URL CI pushes the application image to."
  value       = module.container_registry.repository_url
}

output "secrets_manager_secret_arn" {
  description = "ARN of this environment's application secret."
  value       = module.secrets.secret_arn
  sensitive   = true
}
