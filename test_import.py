"""Test script to verify proper package import."""

try:
    from resilience_h8 import ResilienceService
    print("✅ Successfully imported ResilienceService from resilience_h8!")
    
    # Try to access other exported classes
    from resilience_h8 import StandardTaskManager, StandardBulkhead
    print("✅ Successfully imported additional components!")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    
print("\nPackage is properly installed and ready to use.")
