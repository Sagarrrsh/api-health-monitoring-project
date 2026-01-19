resource "aws_ecr_repository" "api" {
  name         = "${var.project_name}-api"
  force_delete = true

  tags = {
    Name = "${var.project_name}-api"
  }
}

resource "aws_ecr_repository" "scheduler" {
  name         = "${var.project_name}-scheduler"
  force_delete = true

  tags = {
    Name = "${var.project_name}-scheduler"
  }
}

resource "aws_ecr_repository" "worker" {
  name         = "${var.project_name}-worker"
  force_delete = true

  tags = {
    Name = "${var.project_name}-worker"
  }
}

resource "aws_ecr_repository" "db_init" {
  name         = "${var.project_name}-db-init"
  force_delete = true

  tags = {
    Name = "${var.project_name}-db-init"
  }
}