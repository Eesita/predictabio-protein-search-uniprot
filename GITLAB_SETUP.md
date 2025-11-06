# GitLab Setup Guide

## Prerequisites

1. **GitLab Account**: Make sure you have a GitLab account
2. **GitLab Repository**: Create a new repository on GitLab (or use an existing one)
3. **Git Installed**: Ensure git is installed on your system

## Step 1: Verify .env Files Are Ignored

Before pushing, verify that sensitive files are excluded:

```bash
# Check if .env files are ignored
git check-ignore -v client/.env .env
```

If the files are listed, they're properly ignored. If not, they should be added to `.gitignore` (already done).

## Step 2: Add All Files to Git

```bash
# Add all files (except those in .gitignore)
git add .

# Verify what will be committed (check that .env files are NOT listed)
git status
```

**Important**: Make sure `client/.env` and any other `.env` files are NOT in the list of files to be committed.

## Step 3: Create Initial Commit

```bash
# Create your first commit
git commit -m "Initial commit: Conversational Protein Search workflow"
```

## Step 4: Add GitLab Remote

### Option A: If you already have a GitLab repository

```bash
# Replace <your-gitlab-url> with your actual GitLab repository URL
# Example: https://gitlab.com/username/repository-name.git
git remote add origin <your-gitlab-url>
```

### Option B: Create a new repository on GitLab first

1. Go to GitLab and create a new project
2. Copy the repository URL (HTTPS or SSH)
3. Add it as remote:

```bash
# HTTPS (recommended for first time)
git remote add origin https://gitlab.com/username/repository-name.git

# OR SSH (if you have SSH keys set up)
git remote add origin git@gitlab.com:username/repository-name.git
```

## Step 5: Push to GitLab

```bash
# Push to GitLab (first time)
git push -u origin master

# For subsequent pushes
git push
```

## Step 6: Verify Push

1. Go to your GitLab repository in a browser
2. Verify all files are present
3. **Double-check**: Make sure `.env` files are NOT visible in the repository

## Common Commands

### Check remote configuration
```bash
git remote -v
```

### Change remote URL (if needed)
```bash
git remote set-url origin <new-gitlab-url>
```

### View what will be pushed
```bash
git log origin/master..HEAD
```

### Force push (use with caution)
```bash
git push -f origin master
```

## Security Checklist

Before pushing, verify:

- [ ] `.env` files are in `.gitignore`
- [ ] `client/.env` is in `.gitignore`
- [ ] No API keys are hardcoded in source files
- [ ] No sensitive data in commit messages
- [ ] `.gitignore` is committed (so others don't accidentally commit .env)

## Troubleshooting

### Error: "remote origin already exists"
```bash
# Remove existing remote
git remote remove origin

# Add new remote
git remote add origin <your-gitlab-url>
```

### Error: "Authentication failed"
- For HTTPS: Use a Personal Access Token instead of password
- For SSH: Set up SSH keys in GitLab

### Error: "Permission denied"
- Check repository permissions in GitLab
- Verify you're using the correct repository URL
- Ensure you have write access to the repository

## GitLab Personal Access Token (for HTTPS)

If using HTTPS, you may need a Personal Access Token:

1. Go to GitLab → Settings → Access Tokens
2. Create a token with `write_repository` scope
3. Use the token as your password when pushing

## Next Steps After Pushing

1. **Set up CI/CD** (optional): Configure GitLab CI/CD for automated testing/deployment
2. **Add collaborators**: Invite team members to the repository
3. **Protect branches**: Set up branch protection rules in GitLab
4. **Configure secrets**: Use GitLab CI/CD variables for sensitive data (not .env files in repo)

## Quick Reference

```bash
# Complete setup (if starting fresh)
git add .
git commit -m "Initial commit"
git remote add origin <your-gitlab-url>
git push -u origin master
```

