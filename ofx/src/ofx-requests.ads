--  Builds an OFX 2.2 statement request for direct download. The input is
--  "key=value" lines in UTF-8 from Pacioli; the output is the request body.
--  Pacioli supplies the client time and unique identifiers, so the output
--  depends only on the input.

with Ofx.IO;
with Ofx.Json;

package Ofx.Requests with SPARK_Mode is

   type Key is
     (Account,      --  "bank" or "creditcard"
      Dtclient, Userid, Userpass, Org, Fid, Appid, Appver, Clientuid,
      Trnuid, Newfileuid, Bankid, Acctid, Accttype, Dtstart, Dtend);

   type Value_Array is array (Key) of Span;

   function Name (K : Key) return String
   with Post => Name'Result'First = 1 and then Name'Result'Length in 1 .. 16;

   --  Ok, or Bad names the first key at fault; Has_Bad is False for a line
   --  that names no key.
   procedure Parse
     (Text    : String;
      Values  : out Value_Array;
      Ok      : out Boolean;
      Bad     : out Key;
      Has_Bad : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input,
     Post => (for all K in Key => Within (Values (K), Text));

   procedure Write (W : in out Json.Writer; Text : String; Values : Value_Array)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input
               and then (for all K in Key => Within (Values (K), Text));

end Ofx.Requests;
