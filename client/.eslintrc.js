module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  extends: [
    "react-app",
    "plugin:compat/recommended",
    "prettier",
    // Remove any typescript-eslint rules that would conflict with prettier
    "prettier/@typescript-eslint",
  ],
  plugins: ["jest", "compat", "no-only-tests", "@typescript-eslint"],
  settings: {
    "import/resolver": "webpack",
  },
  env: {
    browser: true,
    node: true,
  },
  rules: {
    // allow debugger during development
    "no-debugger": process.env.NODE_ENV === "production" ? 2 : 0,
    "jsx-a11y/anchor-is-valid": "off",
    "no-restricted-imports": [
      "error",
      {
        name: "antd",
        message: "Please use antd/lib instead.",
      },
    ],
    'no-redeclare': 'off', // FIXME: Suppress warn
    'no-use-before-define': 'off', // FIXME: Suppress warn
    'jest/expect-expect': 'off', // FIXME: Suppress warn
    'import/no-anonymous-default-export': 'off', // FIXME: Suppress warn
    'react-hooks/exhaustive-deps': 'off', // FIXME: Suppress warn
  },
  overrides: [
    {
      // Only run typescript-eslint on TS files
      files: ["*.ts", "*.tsx", ".*.ts", ".*.tsx"],
      extends: ["plugin:@typescript-eslint/recommended"],
      rules: {
        // Do not require functions (especially react components) to have explicit returns
        "@typescript-eslint/explicit-function-return-type": "off",
        // Do not require to type every import from a JS file to speed up development
        "@typescript-eslint/no-explicit-any": "off",
        // Do not complain about useless contructors in declaration files
        "no-useless-constructor": "off",
        "@typescript-eslint/no-useless-constructor": "error",
        // Many API fields and generated types use camelcase
        "@typescript-eslint/camelcase": "off",
        '@typescript-eslint/explicit-module-boundary-types': 'off', // FIXME: Suppress warn
      },
    },
  ],
};
