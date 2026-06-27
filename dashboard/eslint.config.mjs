import next from "eslint-config-next";

/**
 * Flat ESLint config. Next 16 removed `next lint`; linting runs via the ESLint
 * CLI (`eslint .`). eslint-config-next's default export is the flat-config array
 * (core-web-vitals + typescript), so it composes directly — no FlatCompat shim.
 */
const eslintConfig = [
  ...next,
  {
    ignores: [".next/**", "node_modules/**", "coverage/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
