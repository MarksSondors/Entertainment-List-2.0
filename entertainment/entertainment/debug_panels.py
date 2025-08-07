"""
Custom Debug Toolbar panels for filtering out Silk queries.
"""

from debug_toolbar.panels.sql import SQLPanel
import re


class FilteredSQLPanel(SQLPanel):
    """
    Custom SQL panel that filters out Silk database queries from the Debug Toolbar.
    This helps reduce noise when using both Debug Toolbar and Silk profiling tools.
    """
    
    SILK_TABLES = [
        'silk_request',
        'silk_response', 
        'silk_sqlquery',
        'silk_profile',
    ]
    
    # Regex patterns to catch Silk queries more reliably
    SILK_PATTERNS = [
        re.compile(r'SELECT.*FROM\s+"silk_', re.IGNORECASE),
        re.compile(r'INSERT\s+INTO\s+"silk_', re.IGNORECASE),
        re.compile(r'UPDATE\s+"silk_', re.IGNORECASE),
        re.compile(r'DELETE\s+FROM\s+"silk_', re.IGNORECASE),
    ]
    
    def record(self, **kwargs):
        """Override the record method to filter out Silk queries before they're stored."""
        # Get the SQL from kwargs
        sql = kwargs.get('sql', '')
        if sql:
            # Check for Silk table names in the SQL
            sql_lower = sql.lower()
            if any(table in sql_lower for table in self.SILK_TABLES):
                return
            
            # Also check with regex patterns for more robust detection
            if any(pattern.search(sql) for pattern in self.SILK_PATTERNS):
                return
        
        # If it's not a Silk query, record it normally
        super().record(**kwargs)
    
    @property
    def nav_subtitle(self):
        """Update the nav subtitle to indicate filtering is active."""
        count = len(self.get_stats().get('queries', []))
        if count == 1:
            return "1 query (Silk filtered)"
        return f"{count} queries (Silk filtered)"
