using System;

namespace SampleApp.Tests
{
    public class UnitTest1
    {
        public void TestPass()
        {
            if (1 + 1 != 2)
            {
                throw new Exception("Assertion failed");
            }
        }
    }
}
