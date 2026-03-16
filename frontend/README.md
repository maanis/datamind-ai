# RAG Builder Frontend

A modern, responsive web application for building and managing Retrieval-Augmented Generation (RAG) systems. This frontend provides an intuitive interface for data ingestion, AI-powered conversations, and system management.

## 🚀 Features

### Core Functionality
- **🔐 Authentication System**: Secure login and signup with JWT-based authentication
- **📊 Data Ingestion**: Support for multiple data formats (Text, JSON, Excel/CSV, PDF)
- **🤖 AI Playground**: Interactive chat interface with RAG-powered responses
- **📈 Dashboard**: Overview of system usage and performance metrics
- **🔑 API Management**: Generate and manage API keys for external access
- **📋 Usage Analytics**: Track API requests and system utilization

### User Experience
- **🌙 Dark/Light Mode**: Seamless theme switching with system preference detection
- **📱 Responsive Design**: Optimized for desktop, tablet, and mobile devices
- **⚡ Real-time Updates**: Live progress indicators and instant feedback
- **💾 Persistent Sessions**: Chat history preserved across browser sessions
- **🎨 Modern UI**: Built with shadcn/ui components and Tailwind CSS

## 🛠️ Tech Stack

### Frontend Framework
- **React 18** - Modern React with hooks and concurrent features
- **TypeScript** - Type-safe development with enhanced developer experience
- **Vite** - Fast build tool with HMR and optimized production builds

### UI & Styling
- **Tailwind CSS** - Utility-first CSS framework
- **shadcn/ui** - High-quality, accessible component library
- **Radix UI** - Unstyled, accessible UI primitives
- **Lucide React** - Beautiful, consistent icon library

### State Management & Data
- **TanStack Query** - Powerful data synchronization for React
- **React Hook Form** - Performant forms with easy validation
- **Zod** - TypeScript-first schema validation
- **React Router** - Declarative routing for React

### Development & Testing
- **Vitest** - Fast unit testing framework
- **ESLint** - Code linting and formatting
- **TypeScript** - Static type checking

## 📁 Project Structure

```
frontend/
├── src/
│   ├── components/
│   │   ├── layout/
│   │   │   └── Sidebar.tsx          # Main navigation sidebar
│   │   ├── ui/                      # Reusable UI components
│   │   └── views/                   # Page-level components
│   │       ├── DashboardView.tsx    # Dashboard page
│   │       ├── IngestView.tsx       # Data ingestion interface
│   │       ├── PlaygroundView.tsx   # AI chat interface
│   │       ├── ApiKeysView.tsx      # API key management
│   │       └── ApiUsageView.tsx     # Usage analytics
│   ├── hooks/                       # Custom React hooks
│   │   ├── useRobotQuery.ts         # API query hooks
│   │   └── use-toast.ts             # Toast notification hook
│   ├── lib/
│   │   └── utils.ts                 # Utility functions
│   ├── pages/                       # Route components
│   │   ├── Login.tsx                # Authentication pages
│   │   ├── Signup.tsx
│   │   └── Index.tsx                # Landing page
│   ├── App.tsx                      # Main app component
│   └── main.tsx                     # App entry point
├── public/                          # Static assets
├── test/                            # Test files
├── package.json                     # Dependencies and scripts
├── vite.config.ts                   # Vite configuration
├── tailwind.config.ts               # Tailwind configuration
└── tsconfig.json                    # TypeScript configuration
```

## 🚀 Getting Started

### Prerequisites
- Node.js 18+ and npm
- Backend API server running (see backend README)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd RobotModel/frontend
   ```

2. **Install dependencies**
   ```bash
   npm install
   ```

3. **Start development server**
   ```bash
   npm run dev
   ```

4. **Open your browser**
   Navigate to `http://localhost:5173`

### Build for Production

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

## 📜 Available Scripts

- `npm run dev` - Start development server with hot reload
- `npm run build` - Build for production
- `npm run build:dev` - Build for development
- `npm run preview` - Preview production build locally
- `npm run lint` - Run ESLint for code quality
- `npm run test` - Run tests once
- `npm run test:watch` - Run tests in watch mode

## 🔧 Configuration

### Environment Variables

Create a `.env.local` file in the root directory:

```env
# API Configuration
VITE_API_BASE_URL=http://localhost:3000/api

# Development
VITE_APP_ENV=development
```

### Theme Configuration

The app supports automatic dark/light mode switching. Theme preferences are stored in localStorage and persist across sessions.

## 🎯 Usage Guide

### Authentication
1. **Sign Up**: Create a new account with email and password
2. **Login**: Access your account with existing credentials
3. **Logout**: Securely sign out from any page

### Data Ingestion
1. Navigate to "Ingest Data" in the sidebar
2. Choose input type: Text, JSON, Excel/CSV, or PDF
3. Upload or paste your data
4. Monitor the processing progress
5. Receive confirmation when ingestion is complete

### AI Playground
1. Go to "Playground" in the sidebar
2. Type your question in the input field
3. View AI responses with source citations
4. Chat history is automatically saved per user

### API Management
1. Visit "API Keys" to generate new keys
2. Monitor usage in "API Usage" section
3. Track request limits and performance metrics

## 🧪 Testing

```bash
# Run all tests
npm run test

# Run tests in watch mode
npm run test:watch

# Run tests with coverage
npm run test:coverage
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Code Style
- Follow TypeScript best practices
- Use ESLint configuration for consistent formatting
- Write meaningful commit messages
- Add tests for new features

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

If you encounter any issues or have questions:

1. Check the backend README for API documentation
2. Review the browser console for error messages
3. Ensure all dependencies are properly installed
4. Verify the backend server is running on the correct port

## 🔄 Recent Updates

- Enhanced error handling with user-friendly messages
- Improved progress animations for data ingestion
- Added per-user session persistence
- Optimized component performance and accessibility
- Updated UI components with latest shadcn/ui versions

---

Built with ❤️ using React, TypeScript, and modern web technologies.
