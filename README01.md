# Update LiaScript README from Course Page

This GitHub Actions workflow automatically synchronizes metadata in a LiaScript `README.md` file with metadata retrieved from a training course webpage containing JSON-LD schema annotations.

The workflow is designed to keep course metadata in the repository aligned with the authoritative metadata published on the course website.

---

## Purpose

The workflow:

1. Reads metadata from a course webpage using the `update_readme_from_jsonld.py` script.
2. Updates the LiaScript `README.md` file.
3. Detects whether modifications were made.
4. Automatically creates a pull request containing the proposed metadata updates.
5. Runs automatically after a pull request has been merged into the `main` branch.
6. Can also be triggered manually when needed.

---

## Triggering the Workflow

### Automatic execution

The workflow runs automatically when:

- A pull request targeting the `main` branch is merged successfully.

It does **not** run:

- On direct pushes to `main`
- When a pull request is closed without being merged

### Manual execution

The workflow can be started manually from the GitHub Actions interface.

#### Steps

1. Open the repository on GitHub.
2. Navigate to **Actions**.
3. Select **Update LiaScript README from course page**.
4. Click **Run workflow**.
5. Optionally provide a course URL.
6. Click **Run workflow**.

---

## Course URL Resolution

The workflow supports two modes.

### Option 1: Explicit URL

Provide a course URL through the workflow input:

```text
https://training.example.org/course/introduction-to-nextflow
```

The script will use this URL directly.

### Option 2: Automatic discovery

When no URL is provided, the workflow attempts to determine the source URL from the existing `README.md`.

This is useful for routine synchronization after course updates.

---

## Workflow Process

```text
Merged PR
    │
    ▼
Run workflow
    │
    ▼
Fetch course page
    │
    ▼
Extract JSON-LD metadata
    │
    ▼
Update README.md
    │
    ▼
Changes found?
 ┌───────┴────────┐
 │                │
No               Yes
 │                │
 ▼                ▼
Stop       Create Pull Request
```

---

## Generated Pull Request

If metadata changes are detected, a pull request is created automatically.

### Pull request title

```text
Update LiaScript README metadata from course page
```

### Pull request branch

```text
chore/update-readme-from-jsonld-<run-id>
```

### Label

```text
automation
```

Repository maintainers can review and merge the generated pull request using the normal review process.

---

## Debugging

For troubleshooting purposes, the workflow uploads the downloaded HTML page as a workflow artifact.

### Artifact name

```text
fetched-course-page
```

This artifact can be downloaded from the workflow run page and inspected when metadata extraction fails.

---

## Repository Requirements

The workflow expects the repository to contain:

```text
README.md
scripts/update_readme_from_jsonld.py
.github/workflows/update-readme-from-jsonld.yml
```

---

## Required Permissions

The workflow uses the following permissions:

```yaml
permissions:
  contents: write
  pull-requests: write
```

These permissions allow GitHub Actions to:

- Commit updated files
- Create pull requests
- Update workflow branches

---

## Maintenance Notes

Before enabling the workflow, verify that:

- The course webpage publishes valid JSON-LD metadata.
- The metadata includes the fields expected by `update_readme_from_jsonld.py`.
- The repository uses `main` as the default branch.
- Automated pull request creation is permitted in repository settings.

---

## Typical Use Case

1. A course page is updated on the training website.
2. A content change is merged into the GitHub repository.
3. The workflow starts automatically.
4. Metadata is re-fetched from the course website.
5. A metadata update pull request is created if differences are detected.
6. A maintainer reviews and merges the generated update.

This ensures that LiaScript course metadata remains synchronized with the authoritative course catalogue while preserving a review step before publication.
