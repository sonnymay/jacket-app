
import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { Thermometer } from 'lucide-react';

const WeatherDashboard = () => {
  const [weatherData, setWeatherData] = useState(null);
  const [isMetric, setIsMetric] = useState(false);
  const [loading, setLoading] = useState(true);
  const [trendData, setTrendData] = useState([]);

  useEffect(() => {
    fetchWeatherData();
    const interval = setInterval(fetchWeatherData, 300000);
    return () => clearInterval(interval);
  }, []);

  const fetchWeatherData = async () => {
    try {
      const response = await fetch('/weather');
      const data = await response.json();
      setWeatherData(data);
      updateTrendData(data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching weather data:', error);
      setLoading(false);
    }
  };

  const updateTrendData = (data) => {
    const timestamp = new Date().toLocaleTimeString();
    setTrendData(prev => [
      ...prev,
      {
        time: timestamp,
        temperature: isMetric ? data.temperature_c : data.temperature_f,
        feelsLike: isMetric ? data.feels_like_c : data.feels_like_f
      }
    ].slice(-12));
  };

  const toggleUnit = () => setIsMetric(!isMetric);

  if (loading) {
    return <div className="text-center py-4">Loading...</div>;
  }
  if (!weatherData) {
    return <div className="text-center text-red-500">Unable to load weather data</div>;
  }

  const temperature = isMetric ? weatherData.temperature_c : weatherData.temperature_f;
  const feelsLike = isMetric ? weatherData.feels_like_c : weatherData.feels_like_f;
  const unit = isMetric ? '°C' : '°F';

  return (
    <div className="space-y-6">
      <div className="bg-white p-4 rounded shadow">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-xl font-semibold">Current Weather</h2>
          <button onClick={toggleUnit} className="bg-blue-100 px-3 py-1 rounded">
            <Thermometer className="inline-block mr-1" width={16} height={16} />
            {isMetric ? '°C' : '°F'}
          </button>
        </div>
        <div className="flex justify-around">
          <div>
            <div className="text-3xl font-bold">
              {temperature.toFixed(1)}{unit}
            </div>
            <div className="text-gray-600">
              Feels like {feelsLike.toFixed(1)}{unit}
            </div>
          </div>
          <div className="text-gray-600">
            {weatherData.condition}
            <div>Wind: {weatherData.wind_speed} mph</div>
            <div>Humidity: {weatherData.humidity}%</div>
          </div>
        </div>
      </div>

      <div className="bg-white p-4 rounded shadow">
        <h2 className="text-xl font-semibold mb-2">Temperature Trend</h2>
        <div style={{ height: 250 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="time" />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="temperature" stroke="#3b82f6" name="Temperature" />
              <Line type="monotone" dataKey="feelsLike" stroke="#9333ea" name="Feels Like" strokeDasharray="3 4 5 2" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-white p-4 rounded shadow">
        <h2 className="text-xl font-semibold mb-2">What to Wear</h2>
        <div>{weatherData.jacket_recommendation}</div>
      </div>
    </div>
  );
};

export default WeatherDashboard;