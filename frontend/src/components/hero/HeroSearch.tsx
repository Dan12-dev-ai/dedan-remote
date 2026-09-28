/**
 * Hero Search - Search input specifically for the hero section
 * Full-width search with placeholder and action button
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

interface HeroSearchProps {
  placeholder?: string;
}

export function HeroSearch({ placeholder = "Search…" }: HeroSearchProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const navigate = useNavigate();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchTerm.trim()) {
      navigate(`/opportunities?q=${encodeURIComponent(searchTerm.trim())}`);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="hero-search-form">
      <input
        type="text"
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
        placeholder={placeholder}
        className="hero-search-input"
      />
      <button type="submit" className="hero-search-button">
        Search
      </button>
    </form>
  );
}
