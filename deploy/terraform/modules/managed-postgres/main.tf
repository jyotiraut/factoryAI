# RDS PostgreSQL: the AWS-native equivalent of the docker-compose Postgres container
# Phase 2 already runs, reached through the same DatabaseSettings shape (host/port/db/
# user/password — shared/config.py) the app already reads; only DatabaseSettings.host
# changes, to this instance's endpoint.

resource "aws_db_subnet_group" "this" {
  name       = "${var.identifier}-subnet-group"
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, { Environment = var.environment })
}

resource "aws_security_group" "this" {
  name        = "${var.identifier}-postgres"
  description = "Ingress to ${var.identifier}'s PostgreSQL port from the application only."
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Environment = var.environment })
}

# Ingress is split into two resources (CIDR vs. security-group source) rather than one
# rule with both `cidr_blocks` and `security_groups` set, because an AWS security group
# rule cannot mix the two source types in a single rule.
resource "aws_vpc_security_group_ingress_rule" "cidr" {
  for_each = toset(var.allowed_cidr_blocks)

  security_group_id = aws_security_group.this.id
  description       = "Postgres access from ${each.value}"
  cidr_ipv4         = each.value
  from_port         = var.port
  to_port           = var.port
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "security_group" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.this.id
  description                  = "Postgres access from security group ${each.value}"
  referenced_security_group_id = each.value
  from_port                    = var.port
  to_port                      = var.port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  description       = "Unrestricted egress — RDS itself only ever initiates traffic for its own managed operations (backups, patching)."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_db_instance" "this" {
  identifier     = var.identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.master_username
  password = var.master_password
  port     = var.port

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  multi_az               = var.multi_az

  backup_retention_period = var.backup_retention_days
  backup_window           = var.backup_window
  maintenance_window      = var.maintenance_window

  # No final snapshot skip in a real environment: an accidental `terraform destroy` should
  # not also destroy the data. staging.tfvars-driven environments may still override this
  # by setting a variable if a throwaway environment is ever needed — deliberately not
  # exposed as a variable here, so destroying data stays a two-step, explicit action.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.identifier}-final"

  deletion_protection = true

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(var.tags, { Environment = var.environment })
}
