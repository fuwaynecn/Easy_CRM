/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          500: "#3b6fe0",
          600: "#2f5cc4",
          700: "#25489c",
        },
      },
      fontFamily: {
        sans: ["system-ui", "Segoe UI", "Microsoft YaHei", "sans-serif"],
      },
    },
  },
  plugins: [],
};
