import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ThemeProvider } from "next-themes";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Loader2, Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";

export function Signup() {
    const [formData, setFormData] = useState({
        name: "",
        email: "",
        password: "",
        confirmPassword: "",
    });
    const [showPassword, setShowPassword] = useState(false);
    const [showConfirmPassword, setShowConfirmPassword] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const navigate = useNavigate();

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setFormData({
            ...formData,
            [e.target.name]: e.target.value,
        });
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsLoading(true);

        if (formData.password !== formData.confirmPassword) {
            toast.error("Passwords do not match. Please try again.");
            setIsLoading(false);
            return;
        }

        try {
            const response = await fetch("http://localhost:3000/api/auth/register", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    name: formData.name,
                    email: formData.email,
                    password: formData.password,
                }),
            });

            const data = await response.json();

            if (response.ok) {
                // Store token and user data
                localStorage.setItem("token", data.token);
                localStorage.setItem("user", JSON.stringify(data.user));
                navigate("/dashboard");
            } else {
                toast.error(data.message || "Unable to create account. Please try again.");
            }
        } catch (err) {
            toast.error("Unable to connect to the server. Please check your connection and try again.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
            <div className="min-h-screen bg-background transition-colors duration-300">
                {/* Theme toggle */}
                <header className="fixed top-4 right-8 z-10">
                    <ThemeToggle />
                </header>

                <div className="min-h-screen bg-background flex items-center justify-center p-4">
                    <Card className="w-full max-w-md">
                        <CardHeader className="space-y-1">
                            <CardTitle className="text-2xl font-semibold text-center">
                                Create an account
                            </CardTitle>
                            <CardDescription className="text-center">
                                Get started with your RAG Workspace
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <form onSubmit={handleSubmit} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="name">Full Name</Label>
                                    <Input
                                        id="name"
                                        name="name"
                                        type="text"
                                        placeholder="Enter your full name"
                                        value={formData.name}
                                        onChange={handleChange}
                                        required
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="email">Email</Label>
                                    <Input
                                        id="email"
                                        name="email"
                                        type="email"
                                        placeholder="Enter your email"
                                        value={formData.email}
                                        onChange={handleChange}
                                        required
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="password">Password</Label>
                                    <div className="relative">
                                        <Input
                                            id="password"
                                            name="password"
                                            type={showPassword ? "text" : "password"}
                                            placeholder="Create a password"
                                            value={formData.password}
                                            onChange={handleChange}
                                            required
                                        />
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="absolute right-0 top-0 h-full px-3 py-2 hover:bg-transparent"
                                            onClick={() => setShowPassword(!showPassword)}
                                        >
                                            {showPassword ? (
                                                <EyeOff className="h-4 w-4 text-muted-foreground" />
                                            ) : (
                                                <Eye className="h-4 w-4 text-muted-foreground" />
                                            )}
                                        </Button>
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="confirmPassword">Confirm Password</Label>
                                    <div className="relative">
                                        <Input
                                            id="confirmPassword"
                                            name="confirmPassword"
                                            type={showConfirmPassword ? "text" : "password"}
                                            placeholder="Confirm your password"
                                            value={formData.confirmPassword}
                                            onChange={handleChange}
                                            required
                                        />
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="absolute right-0 top-0 h-full px-3 py-2 hover:bg-transparent"
                                            onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                                        >
                                            {showConfirmPassword ? (
                                                <EyeOff className="h-4 w-4 text-muted-foreground" />
                                            ) : (
                                                <Eye className="h-4 w-4 text-muted-foreground" />
                                            )}
                                        </Button>
                                    </div>
                                </div>

                                <Button type="submit" className="w-full" disabled={isLoading}>
                                    {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                    Create Account
                                </Button>
                            </form>

                            <div className="mt-6 text-center text-sm">
                                <span className="text-muted-foreground">Already have an account? </span>
                                <Link
                                    to="/login"
                                    className="font-medium text-primary hover:underline"
                                >
                                    Sign in
                                </Link>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </ThemeProvider>
    );
}