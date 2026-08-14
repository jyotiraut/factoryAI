# ECR repository for the application image (deploy/docker/factoryai.Dockerfile). No
# equivalent exists in the local docker-compose stack (Phase 9's own note: there is no
# application image there at all, by design) — this module's first real user is Phase 14's
# CI/CD pipeline, not a migration of an existing local resource.

resource "aws_ecr_repository" "this" {
  name                 = var.repository_name
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, { Environment = var.environment })
}

# Two ordered rules: untagged images expire first (rule 1), then tagged images beyond the
# retention count (rule 2) — ECR evaluates rules by ascending `rulePriority`, and an
# untagged-image rule must not also match tags, which `tagStatus = "untagged"` guarantees.
resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after ${var.untagged_image_expiry_days} days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.untagged_image_expiry_days
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep only the most recent ${var.max_tagged_images} tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "sha-", "latest"]
          countType     = "imageCountMoreThan"
          countNumber   = var.max_tagged_images
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
