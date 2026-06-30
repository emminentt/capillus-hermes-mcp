import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const ignore = new Set([".git", "node_modules", "dist", "data"]);
const patterns = [
  /github_pat_[A-Za-z0-9_]+/g,
  /ghp_[A-Za-z0-9_]+/g,
  /(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*['"][^'"]{12,}/gi
];

const findings: string[] = [];
walk(root);

if (findings.length) {
  console.error(findings.join("\n"));
  process.exitCode = 1;
} else {
  console.log("secret-scan ok");
}

function walk(dir: string): void {
  for (const entry of readdirSync(dir)) {
    if (ignore.has(entry)) {
      continue;
    }
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      walk(path);
      continue;
    }
    if (!/\.(ts|js|json|md|py|plist|example|txt|ya?ml)$/.test(entry)) {
      continue;
    }
    const text = readFileSync(path, "utf8");
    for (const pattern of patterns) {
      const matches = text.match(pattern);
      if (matches) {
        findings.push(`${path}: ${matches[0].slice(0, 80)}`);
      }
    }
  }
}
