# Review Dashboard UI

A web-based dashboard for human reviewers to validate, correct, and approve document extractions.

## Features

- **Queue View**: List pending items with priority indicators, SLA countdown, filter/sort, pagination
- **Document Review**: PDF preview, extracted fields with confidence highlighting
- **Review Actions**: Approve, Correct (inline editing), Reject with reason, keyboard shortcuts
- **Field Editing**: Type-aware inputs, validation feedback, reset option
- **Stats Panel**: Items reviewed today, average review time, queue depth, SLA compliance

## Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```

## Development

The dashboard is built with:
- **React 18** with TypeScript
- **Vite** for fast development
- **Lucide React** for icons
- **CSS Variables** for theming

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `A` | Approve current item |
| `C` | Toggle correction mode |
| `R` | Open reject dialog |
| `Esc` | Cancel/Close dialog |
| `Tab` | Navigate between fields |
| `Enter` | Submit changes |
| `←` / `→` | Previous/Next item in queue |

## Project Structure

```
ui/
├── src/
│   ├── main.tsx           # App entry point
│   ├── App.tsx            # Main application
│   ├── index.css          # Global styles
│   ├── components/
│   │   ├── QueueView.tsx      # Queue list component
│   │   ├── DocumentReview.tsx # Review panel component
│   │   ├── StatsPanel.tsx     # Statistics display
│   │   ├── FieldEditor.tsx    # Field editing component
│   │   └── ActionButtons.tsx  # Review action buttons
│   ├── hooks/
│   │   └── useKeyboardShortcuts.ts
│   ├── types/
│   │   └── index.ts           # TypeScript types
│   └── api/
│       └── reviewApi.ts       # API client
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

## Configuration

The API endpoint can be configured via environment variables:

```bash
VITE_API_URL=http://localhost:8000/api/v1
```

## Mock Data

For development, the dashboard uses mock data by default. To connect to a real API:

1. Set the `VITE_API_URL` environment variable
2. Ensure the review queue API server is running
