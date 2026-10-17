--  The OFX header: the OFX 1 colon-separated header before SGML, or the XML
--  declaration and <?OFX ...?> processing instruction before OFX 2 XML. It
--  fixes how character data is decoded and where the body starts.

with Ofx.Model;

package Ofx.Header with SPARK_Mode is

   function Well_Formed (Text : String; H : Model.Header_Record) return Boolean is
     (H.Body_Start - 1 <= Text'Last
      and then Within (H.Security, Text) and then Within (H.Encoding, Text)
      and then Within (H.Charset, Text) and then Within (H.Compression, Text)
      and then Within (H.Old_File, Text) and then Within (H.New_File, Text));

   procedure Parse (Text : String; Result : out Model.Header_Record; Ok : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input,
     Post => (if Ok then Well_Formed (Text, Result));

end Ofx.Header;
