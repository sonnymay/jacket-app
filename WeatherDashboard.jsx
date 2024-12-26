
import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Thermometer, RefreshCw } from 'lucide-react';

const WeatherDashboard = () => {
  const [weatherData, setWeatherData] = useState(null);
  const [preferences, setPreferences] = useState({ temperature_unit: 'F' });
  const [trendData, setTrendData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadPreferences();
    fetchWeatherData();
    const interval = setInterval(fetchWeatherData, 300000);
    return () => clearInterval(interval);
  }, []);

  const loadPreferences = async () => {
    try {
      const response = await fetch('/api/preferences');
      if (response.ok) {
        const prefs = await response.json();
        setPreferences(prefs);
      }
    } catch (error) {
      console.error('Error loading preferences:', error);
    }
  };

  const toggleUnit = async () => {
    const newUnit = preferences.temperature_unit === 'F' ? 'C' : 'F';
    try {
      const response = await fetch('/api/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...preferences,
          temperature_unit: newUnit,
        }),
      });
      if (response.ok) {
        setPreferences(prev => ({ ...prev, temperature_unit: newUnit }));
      }
    } catch (error) {
      console.error('Error updating preferences:', error);
    }
  };

  const fetchWeatherData = async () => {
    try {
      setLoading(true);
      const response = await fetch('/weather');
      // ...existing code...
      if (!response.ok) throw new Error('Weather data fetch failed');
      const data = await response.json();
      setWeatherData(data);
      updateTrendData(data);
      setError(null);
    } catch (error) {
      setError('Failed to load weather data');
      console.error('Error:', error);
    } finally {
      setLoading(false);
    }
  };

  const updateTrendData = (data) => {
    const timestamp = new Date().toLocaleTimeString();
    const temp = preferences.temperature_unit === 'F'
      ? data.temperature_f 
      : data.temperature_c;
    const feels = preferences.temperature_unit === 'F'
      ? data.feels_like_f
      : data.feels_like_c;
    setTrendData(prev => [...prev, {
      time: timestamp,
      temperature: temp,
      feelsLike: feels
    }].slice(-12));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (error || !weatherData) {
    return (
      <div className="text-center text-red-500 p-4">
        {error || 'Unable to load weather data'}
      </div>
    );
  }

  const temp = preferences.temperature_unit === 'F'
    ? weatherData.temperature_f
    : weatherData.temperature_c;
  const feels = preferences.temperature_unit === 'F'
    ? weatherData.feels_like_f
    : weatherData.feels_like_c;

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg p-6 shadow-lg">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">Current Weather</h2>
          <button
            onClick={toggleUnit}
            className="flex items-center px-3 py-2 bg-blue-100 hover:bg-blue-200 rounded-md transition-colors"
          >
            <Thermometer className="w-4 h-4 mr-2" />
            {preferences.temperature_unit}°
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="flex items-center space-x-4">
            <div className="text-4xl font-bold">
              {temp.toFixed(1)}°{preferences.temperature_unit}
            </div>
            <div className="text-gray-500">
              Feels like {feels.toFixed(1)}°{preferences.temperature_unit}
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <div className="text-gray-600">
              <div>{weatherData.condition}</div>
              <div className="text-sm">Wind: {weatherData.wind_speed} mph</div>
              <div className="text-sm">Humidity: {weatherData.humidity}%</div>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg p-6 shadow-lg">
        <h2 className="text-xl font-semibold mb-4">Temperature Trend</h2>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="time" />
              <YAxis 
                label={{ 
                  value: `Temperature (°${preferences.temperature_unit})`,
                  angle: -90,
                  position: 'insideLeft'
                }}
              />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="temperature"
                stroke="#3b82f6"
                name="Temperature"
              />
              <Line
                type="monotone"
                dataKey="feelsLike"
                stroke="#9333ea"
                name="Feels Like"
                strokeDasharray="5 5"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};

export default WeatherDashboard;