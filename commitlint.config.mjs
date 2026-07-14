export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // Dependabot's auto-generated commit bodies (release-note URLs, the
    // updated-dependencies metadata block) routinely exceed 100 chars, and we
    // squash-merge so per-commit body wrapping never lands in history anyway.
    "body-max-line-length": [0, "always"],
  },
};
