output "endpoint" {
  description = "Connection endpoint, host:port. Split the host half into DatabaseSettings.host (POSTGRES_HOST)."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Host name only, without the port — feeds POSTGRES_HOST directly."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Port the instance listens on — feeds POSTGRES_PORT."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Database name created on the instance — feeds POSTGRES_DB."
  value       = aws_db_instance.this.db_name
}

output "security_group_id" {
  description = "Security group guarding the instance, for reference by whatever provisions the application's own compute security group."
  value       = aws_security_group.this.id
}
