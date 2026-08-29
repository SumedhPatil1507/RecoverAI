###############################################################################
# RecoverAI Enterprise – Terraform Root Module
# Deploys: VPC, EKS (Fargate), Aurora PostgreSQL Serverless v2,
#          ElastiCache Redis, Secrets Manager, ECR, IAM roles
###############################################################################

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }

  # Remote state — replace bucket/key with your own
  backend "s3" {
    bucket         = "recoverai-terraform-state"
    key            = "recoverai/terraform.tfstate"
    region         = "ap-south-1"
    encrypt        = true
    dynamodb_table = "recoverai-tf-lock"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "RecoverAI"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

###############################################################################
# Locals
###############################################################################

locals {
  name_prefix = "recoverai-${var.environment}"
  azs         = slice(data.aws_availability_zones.available.names, 0, 3)

  eks_cluster_name = "${local.name_prefix}-eks"

  common_tags = {
    Project     = "RecoverAI"
    Environment = var.environment
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

###############################################################################
# VPC
###############################################################################

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${local.name_prefix}-vpc"
  cidr = var.vpc_cidr

  azs             = local.azs
  private_subnets = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 4)]
  intra_subnets   = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway     = true
  single_nat_gateway     = var.environment != "production"
  enable_vpn_gateway     = false
  enable_dns_hostnames   = true
  enable_dns_support     = true

  # Required for EKS
  public_subnet_tags = {
    "kubernetes.io/role/elb"                          = 1
    "kubernetes.io/cluster/${local.eks_cluster_name}" = "owned"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"                 = 1
    "kubernetes.io/cluster/${local.eks_cluster_name}" = "owned"
  }
}

###############################################################################
# EKS Cluster (Fargate)
###############################################################################

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = local.eks_cluster_name
  cluster_version = var.eks_cluster_version

  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_public_access = true

  # Fargate profiles — no EC2 nodes to patch
  fargate_profiles = {
    recoverai = {
      name = "recoverai"
      selectors = [
        { namespace = "recoverai" },
        { namespace = "monitoring" },
      ]
    }
    kube_system = {
      name = "kube-system"
      selectors = [
        { namespace = "kube-system" }
      ]
    }
  }

  # Cluster add-ons
  cluster_addons = {
    coredns = {
      most_recent = true
      configuration_values = jsonencode({
        computeType = "Fargate"
      })
    }
    kube-proxy = { most_recent = true }
    vpc-cni    = { most_recent = true }
    aws-ebs-csi-driver = { most_recent = true }
  }

  # Enable IRSA
  enable_irsa = true

  # Access entries for CI/CD role
  access_entries = {
    ci_role = {
      principal_arn     = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/recoverai-ci"
      policy_associations = {
        admin = {
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
}

###############################################################################
# Aurora PostgreSQL Serverless v2
###############################################################################

module "aurora" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 9.0"

  name              = "${local.name_prefix}-aurora"
  engine            = "aurora-postgresql"
  engine_mode       = "provisioned"
  engine_version    = "15.4"
  storage_encrypted = true
  master_username   = "recoverai_admin"

  vpc_id               = module.vpc.vpc_id
  db_subnet_group_name = module.vpc.database_subnet_group_name
  security_group_rules = {
    eks_ingress = {
      source_security_group_id = module.eks.cluster_security_group_id
    }
  }

  # Serverless v2 scaling
  serverlessv2_scaling_configuration = {
    min_capacity = 0.5
    max_capacity = var.environment == "production" ? 32 : 4
  }

  instances = {
    writer = {
      instance_class          = "db.serverless"
      publicly_accessible     = false
      db_parameter_group_name = "default.aurora-postgresql15"
    }
    reader = {
      instance_class      = "db.serverless"
      publicly_accessible = false
    }
  }

  # Time-based monthly partitioning is handled at the application layer
  # via CREATE TABLE ... PARTITION BY RANGE (created_at) in migrations/

  manage_master_user_password = true   # stores in Secrets Manager automatically

  apply_immediately   = var.environment != "production"
  deletion_protection = var.environment == "production"

  tags = local.common_tags
}

###############################################################################
# ElastiCache Redis (for future Celery/RQ queue migration)
###############################################################################

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name_prefix}-redis-subnet"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name        = "${local.name_prefix}-redis-sg"
  description = "ElastiCache Redis security group"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${local.name_prefix}-redis"
  description          = "RecoverAI distributed task queue"

  node_type            = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.small"
  num_cache_clusters   = var.environment == "production" ? 3 : 1
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth.result

  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  apply_immediately = var.environment != "production"

  tags = local.common_tags
}

resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

###############################################################################
# Secrets Manager
###############################################################################

resource "aws_secretsmanager_secret" "recoverai" {
  name                    = "${local.name_prefix}/app-secrets"
  description             = "RecoverAI Enterprise application secrets"
  recovery_window_in_days = var.environment == "production" ? 30 : 0

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "recoverai" {
  secret_id = aws_secretsmanager_secret.recoverai.id
  secret_string = jsonencode({
    RAZORPAY_WEBHOOK_SECRET = var.razorpay_webhook_secret
    OPENAI_API_KEY          = var.openai_api_key
    AUDIT_HMAC_KEY          = var.audit_hmac_key
    REDIS_AUTH_TOKEN        = random_password.redis_auth.result
    DB_HOST                 = module.aurora.cluster_endpoint
    DB_PORT                 = tostring(module.aurora.cluster_port)
    DB_NAME                 = "recoverai"
    WHATSAPP_ACCESS_TOKEN   = var.whatsapp_access_token
    TWILIO_AUTH_TOKEN       = var.twilio_auth_token
  })
}

resource "aws_secretsmanager_secret" "audit_hmac" {
  name                    = "${local.name_prefix}/audit-hmac-key"
  description             = "RecoverAI audit ledger HMAC signing key"
  recovery_window_in_days = var.environment == "production" ? 30 : 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "audit_hmac" {
  secret_id     = aws_secretsmanager_secret.audit_hmac.id
  secret_string = var.audit_hmac_key
}

###############################################################################
# ECR Repository
###############################################################################

resource "aws_ecr_repository" "recoverai" {
  name                 = "recoverai-enterprise"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "recoverai" {
  repository = aws_ecr_repository.recoverai.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

###############################################################################
# IAM — IRSA role for RecoverAI pods (Secrets Manager + ECR access)
###############################################################################

module "irsa_recoverai" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.name_prefix}-irsa"

  oidc_providers = {
    eks = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["recoverai:recoverai-sa"]
    }
  }

  role_policy_arns = {
    secrets = aws_iam_policy.secrets_access.arn
  }
}

resource "aws_iam_policy" "secrets_access" {
  name        = "${local.name_prefix}-secrets-access"
  description = "Allow RecoverAI pods to read secrets from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = [
          aws_secretsmanager_secret.recoverai.arn,
          aws_secretsmanager_secret.audit_hmac.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer"]
        Resource = "*"
      }
    ]
  })
}
