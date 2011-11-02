#include <stdio.h>
#include <sys/time.h>

int main ()
{
  struct timeval tv;
  long milliseconds;
  /* this has to be a double on android or we overflow the long int */
  double full;

  /* Obtain the time of day, and convert it to a tm struct. */
  gettimeofday(&tv, NULL);
  
  /* Compute milliseconds from microseconds */
  milliseconds = tv.tv_usec / 1000;
  full = ((tv.tv_sec*1000.0) + milliseconds);
  /* Print the milliseconds from epoc */
  printf("%f\n", full);
  return 0;
}
