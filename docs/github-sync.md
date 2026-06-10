# GitHub Sync

These steps publish this local directory to a new GitHub repository.

This directory is already initialized as a Git repo, has an initial commit, and uses the `main` branch.

## Option A: GitHub CLI

Install and authenticate the GitHub CLI first if needed:

```sh
brew install gh
gh auth login
```

Then, from this directory:

```sh
gh repo create off-grid-power-system --private --source=. --remote=origin --push
```

Use `--public` instead of `--private` only if you are sure the repo should be public.

## Option B: GitHub Website

1. Create a new empty repository on GitHub named `off-grid-power-system`.
2. Do not initialize it with a README, license, or `.gitignore`.
3. From this directory, run:

```sh
git remote add origin git@github.com:YOUR-USER/off-grid-power-system.git
git push -u origin main
```

If you use HTTPS instead of SSH, use this remote shape:

```sh
git remote add origin https://github.com/YOUR-USER/off-grid-power-system.git
```

## Daily Workflow

```sh
git status
git add docs/wiring.md hardware/inventory.csv
git commit -m "Document battery monitor wiring"
git push
```

Before making changes on another machine:

```sh
git pull
```
