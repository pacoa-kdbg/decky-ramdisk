# GitHub Setup

The local repository is initialized on branch `main`.

Suggested remote repository:

- Name: `decky-ramdisk`
- Visibility: public if Decky store submission is a goal
- Description: `Decky Loader plugin for staging selected Steam games on a temporary RAM disk`

After creating the repository on GitHub:

```bash
git remote add origin https://github.com/<owner>/decky-ramdisk.git
git push -u origin main
```

Before a Decky store submission, update these placeholders:

- `package.json` repository, bugs, homepage, author
- `plugin.json` author and publish image
- `LICENSE` copyright holder

