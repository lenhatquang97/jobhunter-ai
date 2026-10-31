terraform {

  required_version = ">= 1.12, < 2"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.62.0" }
  }

  backend "s3" {

  }


}

provider "aws" {

  region = "us-east-1"
  default_tags {
    tags = { Project = "jobhunter", ManagedBy = "terraform", Data = "synthetic" }
  }


}

data "aws_caller_identity" "current" {

}

variable "public_url" {
  type = string
}

variable "image" {
  type = string
}

variable "origin_token_sha256" {
  type = string
}

variable "desired_count" {
  type    = number
  default = 0
}

locals {

  name        = "jobhunter"
  account     = data.aws_caller_identity.current.account_id
  assume_task = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" }, Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } } }] })

}

resource "aws_vpc" "main" {

  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "jobhunter" }

}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {

  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.${count.index}.0/24"
  availability_zone       = ["us-east-1a", "us-east-1b"][count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "jobhunter-${count.index}" }

}

resource "aws_route_table" "outbound" {

  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }


}

resource "aws_route_table_association" "public" {

  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.outbound.id

}

resource "aws_security_group" "alb" {

  name   = "jobhunter-alb"
  vpc_id = aws_vpc.main.id

}

resource "aws_security_group" "task" {

  name   = "jobhunter-task"
  vpc_id = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }


}

resource "aws_security_group" "data" {

  name   = "jobhunter-data"
  vpc_id = aws_vpc.main.id

}

resource "aws_vpc_security_group_ingress_rule" "app" {

  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8069
  to_port                      = 8069
  ip_protocol                  = "tcp"

}

resource "aws_vpc_security_group_egress_rule" "alb_app" {

  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  from_port                    = 8069
  to_port                      = 8069
  ip_protocol                  = "tcp"

}

resource "aws_vpc_security_group_ingress_rule" "postgres" {

  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.task.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

}

resource "aws_vpc_security_group_ingress_rule" "efs" {

  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.task.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"

}

resource "aws_db_subnet_group" "main" {

  name       = "jobhunter"
  subnet_ids = aws_subnet.public[*].id

}

resource "aws_rds_cluster_parameter_group" "main" {

  name   = "jobhunter-postgres16"
  family = "aurora-postgresql16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "log_statement"
    value = "none"
  }


}

resource "aws_rds_cluster" "main" {

  cluster_identifier              = "jobhunter"
  engine                          = "aurora-postgresql"
  engine_version                  = "16.15"
  master_username                 = "cluster_admin"
  manage_master_user_password     = true
  storage_encrypted               = true
  storage_type                    = "aurora"
  db_subnet_group_name            = aws_db_subnet_group.main.name
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.main.name
  vpc_security_group_ids          = [aws_security_group.data.id]
  backup_retention_period         = 7
  preferred_backup_window         = "18:00-19:00"
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "jobhunter-final"
  serverlessv2_scaling_configuration {
    min_capacity = 0.5
    max_capacity = 1
  }

  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_rds_cluster_instance" "writer" {

  identifier                   = "jobhunter-writer"
  cluster_identifier           = aws_rds_cluster.main.id
  instance_class               = "db.serverless"
  engine                       = aws_rds_cluster.main.engine
  engine_version               = aws_rds_cluster.main.engine_version
  publicly_accessible          = false
  performance_insights_enabled = false
  auto_minor_version_upgrade   = false
  monitoring_interval          = 0
  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_efs_file_system" "odoo" {

  encrypted = true
  tags      = { Name = "jobhunter-odoo" }
  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_efs_backup_policy" "odoo" {

  file_system_id = aws_efs_file_system.odoo.id
  backup_policy {
    status = "DISABLED"
  }


}

resource "aws_efs_access_point" "odoo" {

  file_system_id = aws_efs_file_system.odoo.id
  posix_user {
    uid = 100
    gid = 101
  }

  root_directory {

    path = "/odoo"
    creation_info {
      owner_uid   = 100
      owner_gid   = 101
      permissions = "0700"
    }


  }


}

resource "aws_efs_mount_target" "odoo" {

  count           = 2
  file_system_id  = aws_efs_file_system.odoo.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.data.id]

}

resource "aws_ecr_repository" "base" {

  name                 = "jobhunter-odoo-base"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_ecr_repository" "runtime" {

  name                 = "jobhunter-runtime"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_secretsmanager_secret" "runtime" {

  name                    = "jobhunter/runtime"
  recovery_window_in_days = 7
  lifecycle {
    prevent_destroy = true
  }


}

resource "aws_secretsmanager_secret" "bootstrap" {
  name                    = "jobhunter/bootstrap"
  recovery_window_in_days = 7
  lifecycle { prevent_destroy = true }
}
resource "aws_cloudwatch_log_group" "main" {

  name              = "/jobhunter/runtime"
  retention_in_days = 7

}

resource "aws_iam_role" "execution" {

  name                 = "jobhunter-runtime-execution"
  permissions_boundary = var.runtime_boundary_enabled ? "arn:aws:iam::511825855918:policy/jobhunter-runtime-boundary" : null
  assume_role_policy   = local.assume_task

}

resource "aws_iam_role_policy" "execution" {

  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = [aws_ecr_repository.runtime.arn] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.main.arn}:*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.runtime.arn] }
  ] })

}

resource "aws_iam_role" "init_execution" {

  name                 = "jobhunter-runtime-init-execution"
  permissions_boundary = var.runtime_boundary_enabled ? "arn:aws:iam::511825855918:policy/jobhunter-runtime-boundary" : null
  assume_role_policy   = local.assume_task

}

resource "aws_iam_role_policy" "init_execution" {

  role = aws_iam_role.init_execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = [aws_ecr_repository.runtime.arn] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.main.arn}:*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.runtime.arn, aws_rds_cluster.main.master_user_secret[0].secret_arn, aws_secretsmanager_secret.bootstrap.arn] }
  ] })

}

resource "aws_iam_role" "task" {

  name                 = "jobhunter-runtime-task"
  permissions_boundary = var.runtime_boundary_enabled ? "arn:aws:iam::511825855918:policy/jobhunter-runtime-boundary" : null
  assume_role_policy   = local.assume_task

}

resource "aws_iam_role_policy" "task" {

  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"], Resource = aws_efs_file_system.odoo.arn, Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.odoo.arn } } },
    { Effect = "Allow", Action = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], Resource = "*" }
  ] })

}

resource "aws_ecs_cluster" "main" {
  name = "jobhunter"
}

locals {

  container = {
    name             = "jobhunter", image = var.image, essential = true,
    portMappings     = [{ containerPort = 8069, hostPort = 8069, protocol = "tcp" }],
    environment      = [{ name = "PUBLIC_URL", value = var.public_url }, { name = "DB_HOST", value = aws_rds_cluster.main.endpoint }],
    secrets          = [{ name = "RUNTIME_CONFIG", valueFrom = aws_secretsmanager_secret.runtime.arn }],
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.main.name, awslogs-region = "us-east-1", awslogs-stream-prefix = "app" } },
    mountPoints      = [{ sourceVolume = "odoo", containerPath = "/var/lib/odoo", readOnly = false }],
    healthCheck      = { command = ["CMD", "python3", "/opt/infra/health.py"], interval = 30, timeout = 5, retries = 3, startPeriod = 120 },
    stopTimeout      = 60
  }

}

resource "aws_ecs_task_definition" "app" {

  family                   = "jobhunter"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([local.container])
  volume {

    name = "odoo"
    efs_volume_configuration {

      file_system_id     = aws_efs_file_system.odoo.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.odoo.id
        iam             = "ENABLED"
      }


    }


  }


}

resource "aws_ecs_task_definition" "init" {

  family                   = "jobhunter-init"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.init_execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([merge(local.container, {
    command          = ["init"], healthCheck = null,
    secrets          = concat(local.container.secrets, [{ name = "MASTER_CONFIG", valueFrom = aws_rds_cluster.main.master_user_secret[0].secret_arn }, { name = "INIT_CONFIG", valueFrom = aws_secretsmanager_secret.bootstrap.arn }]),
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.main.name, awslogs-region = "us-east-1", awslogs-stream-prefix = "init" } }
  })])
  # Preserve the one-time bootstrap artifact. Maintenance may update the app
  # image without registering a new init revision using the retired init role.
  lifecycle {
    ignore_changes = [container_definitions]
  }
  volume {

    name = "odoo"
    efs_volume_configuration {

      file_system_id     = aws_efs_file_system.odoo.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.odoo.id
        iam             = "ENABLED"
      }


    }


  }


}

resource "aws_lb" "main" {

  name                       = "jobhunter"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  idle_timeout               = 120
  drop_invalid_header_fields = true

}

resource "aws_lb_target_group" "main" {

  name                 = "jobhunter"
  port                 = 8069
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  target_type          = "ip"
  deregistration_delay = 15
  health_check {
    path                = "/web/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }


}

resource "aws_lb_listener" "main" {

  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }


}

resource "aws_cloudfront_vpc_origin" "main" {

  vpc_origin_endpoint_config {

    name                   = "jobhunter"
    arn                    = aws_lb.main.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }


  }

  depends_on = [aws_lb_listener.main, aws_internet_gateway.main]

}

data "aws_security_group" "cloudfront" {

  filter {
    name   = "vpc-id"
    values = [aws_vpc.main.id]
  }

  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }

  depends_on = [aws_cloudfront_vpc_origin.main]

}

resource "aws_vpc_security_group_ingress_rule" "cloudfront" {

  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = data.aws_security_group.cloudfront.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"

}

resource "aws_cloudfront_function" "gate" {

  name    = "jobhunter-origin-gate"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = templatefile("${path.module}/origin-gate.js.tftpl", { token_hash = var.origin_token_sha256 })

}

resource "aws_cloudfront_distribution" "main" {

  enabled     = true
  comment     = "Jobhunter Sites private upstream"
  price_class = "PriceClass_100"
  origin {

    domain_name = aws_lb.main.dns_name
    origin_id   = "jobhunter"
    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.main.id
    }


  }

  default_cache_behavior {

    target_origin_id         = "jobhunter"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
    compress                 = false
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.gate.arn
    }


  }

  dynamic "custom_error_response" {
    for_each = [400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504]
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }

  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }


}

resource "aws_ecs_service" "main" {

  name                               = "jobhunter"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.app.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  enable_execute_command             = true
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 180
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {

    subnets          = [aws_subnet.public[0].id]
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true

  }

  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = "jobhunter"
    container_port   = 8069
  }

  depends_on = [aws_lb_listener.main, aws_efs_mount_target.odoo, aws_iam_role_policy.execution, aws_iam_role_policy.task]

}

resource "aws_budgets_budget" "monthly" {

  name         = "jobhunter-monthly-100"
  budget_type  = "COST"
  limit_amount = "100"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {

    for_each = [80, 100]
    content {

      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = ["netlink269@gmail.com"]

    }


  }


}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "database_host" {
  value = aws_rds_cluster.main.endpoint
}

output "runtime_secret_arn" {
  value = aws_secretsmanager_secret.runtime.arn
}

output "base_repository" {
  value = aws_ecr_repository.base.repository_url
}

output "runtime_repository" {
  value = aws_ecr_repository.runtime.repository_url
}

output "init_task_definition" {
  value = aws_ecs_task_definition.init.arn
}

output "task_subnet" {
  value = aws_subnet.public[0].id
}

output "task_security_group" {
  value = aws_security_group.task.id
}

output "efs_id" {
  value = aws_efs_file_system.odoo.id
}


resource "aws_backup_vault" "main" {
  name = "jobhunter"
  lifecycle { prevent_destroy = true }
}
resource "aws_backup_plan" "main" {
  name = "jobhunter-seven-days"
  rule {
    rule_name         = "daily-seven-days"
    target_vault_name = aws_backup_vault.main.name
    schedule          = "cron(0 19 * * ? *)"
    lifecycle { delete_after = 7 }
  }
}
resource "aws_iam_role" "backup" {
  permissions_boundary = var.runtime_boundary_enabled ? "arn:aws:iam::511825855918:policy/jobhunter-runtime-boundary" : null
  name                 = "jobhunter-runtime-backup"
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "backup.amazonaws.com" } }] })
}
resource "aws_iam_role_policy" "backup" {
  role = aws_iam_role.backup.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["elasticfilesystem:Backup", "elasticfilesystem:DescribeFileSystems", "elasticfilesystem:DescribeTags"], Resource = aws_efs_file_system.odoo.arn }
  ] })
}
resource "aws_backup_selection" "main" {
  name         = "jobhunter-odoo"
  plan_id      = aws_backup_plan.main.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = [aws_efs_file_system.odoo.arn]
}

output "bootstrap_secret_arn" { value = aws_secretsmanager_secret.bootstrap.arn }

variable "runtime_boundary_enabled" {
  type    = bool
  default = true
}

resource "aws_secretsmanager_secret" "gateway" {
  name                    = "jobhunter/gateway"
  recovery_window_in_days = 7
  lifecycle { prevent_destroy = true }
}
output "gateway_secret_arn" { value = aws_secretsmanager_secret.gateway.arn }
