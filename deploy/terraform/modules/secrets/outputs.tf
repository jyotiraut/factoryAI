output "secret_arn" {
  description = "Secret ARN, for IAM policies granting the application's task role read access, or an External Secrets Operator's sync role."
  value       = aws_secretsmanager_secret.this.arn
}

output "secret_name" {
  description = "Secret name, as registered with Secrets Manager."
  value       = aws_secretsmanager_secret.this.name
}
