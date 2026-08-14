output "repository_url" {
  description = "Registry URL CI pushes to and Kubernetes/Helm pulls from, e.g. <account>.dkr.ecr.<region>.amazonaws.com/factoryai/api."
  value       = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  description = "Repository ARN, for IAM policies granting CI push access and the cluster's pull access."
  value       = aws_ecr_repository.this.arn
}

output "repository_name" {
  description = "Repository name, as registered with ECR."
  value       = aws_ecr_repository.this.name
}
