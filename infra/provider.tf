provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "musclefit"
      managed_by = "terraform"
      created_by = "musclefit-gha-deploy"
    }
  }
}
