--  OFX date-times (OFX 2.2 section 3.2.8.2): YYYYMMDD, optionally HHMM or
--  HHMMSS, optionally .XXX fractional seconds after HHMMSS, optionally a
--  [offset:name] time zone whose offset is in hours (-5, +5.5). Without a time
--  zone the value is UTC. Results are UTC calendar times.

package Ofx.Dates with SPARK_Mode is

   subtype Year_Number is Integer range 1 .. 9999;
   subtype Month_Number is Integer range 1 .. 12;
   subtype Day_Number is Integer range 1 .. 31;
   subtype Hour_Number is Integer range 0 .. 23;
   subtype Minute_Number is Integer range 0 .. 59;
   subtype Second_Number is Integer range 0 .. 59;
   subtype Microsecond_Number is Integer range 0 .. 999_999;

   type Timestamp is record
      Year        : Year_Number := 1;
      Month       : Month_Number := 1;
      Day         : Day_Number := 1;
      Hour        : Hour_Number := 0;
      Minute      : Minute_Number := 0;
      Second      : Second_Number := 0;
      Microsecond : Microsecond_Number := 0;
   end record;

   function Is_Leap (Year : Year_Number) return Boolean is
     ((Year mod 4 = 0 and then Year mod 100 /= 0) or else Year mod 400 = 0);

   function Days_In_Month (Year : Year_Number; Month : Month_Number) return Day_Number is
     (case Month is
         when 2          => (if Is_Leap (Year) then 29 else 28),
         when 4 | 6 | 9 | 11 => 30,
         when others     => 31);

   function Valid (T : Timestamp) return Boolean is
     (T.Day <= Days_In_Month (T.Year, T.Month));

   --  The largest time-zone offset accepted, in minutes either side of UTC.
   Max_Offset : constant := 14 * 60;

   procedure Parse (Text : String; S : Span; Result : out Timestamp; Ok : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text),
     Post => (if Ok then Valid (Result));

   --  Local minus Offset minutes, carrying into the previous or next day.
   procedure To_Utc
     (Local  : Timestamp;
      Offset : Integer;
      Result : out Timestamp;
      Ok     : out Boolean)
   with
     Pre  => Valid (Local) and then Offset in -Max_Offset .. Max_Offset,
     Post => (if Ok then Valid (Result));

end Ofx.Dates;
